"""Thin web-search wrapper over the Tavily API (used for achievement how-to
guides). Calls the REST endpoint directly with `requests` — no extra SDK, and
it picks up the truststore SSL fix from config like the Steam client does.

Returns a small, normalized list of {title, url, content} dicts so the agent
can synthesize an answer with citations. Network/credential failures degrade
to an empty list rather than crashing a run.
"""
import hashlib
import json
import os
import time
from pathlib import Path

import requests

from config import TAVILY_API_KEY

_ENDPOINT = "https://api.tavily.com/search"

# Shared, content-keyed search cache. Keeps multi-user Tavily usage bounded: the
# cache key identifies the CONTENT (e.g. a game), not the user, so N users asking
# about the same game cost ONE call. Static-ish data → a long TTL is fine.
_CACHE_DIR = Path(__file__).parent.parent / "data" / "search_cache"
_CACHE_TTL_SECONDS = float(os.environ.get("SEARCH_CACHE_TTL_DAYS", "14")) * 86400


def web_search(query: str, max_results: int = 3) -> list[dict]:
    """Search the web for `query`. Returns up to `max_results` results as
    [{title, url, content}]. Empty list if no key is set or the call fails."""
    if not TAVILY_API_KEY:
        return []
    try:
        resp = requests.post(
            _ENDPOINT,
            json={
                "api_key": TAVILY_API_KEY,
                "query": query,
                "max_results": max_results,
                "search_depth": "basic",
            },
            timeout=15,
        )
        resp.raise_for_status()
        results = resp.json().get("results", [])
    except Exception:
        return []

    return [
        {
            "title":   r.get("title", ""),
            "url":     r.get("url", ""),
            "content": r.get("content", ""),
        }
        for r in results
    ]


def _redis():
    """The shared cache, or None when it isn't configured (local dev, eval).
    Imported LAZILY so the agent package keeps working — and stays importable —
    with no cache layer at all."""
    try:
        from data_layer import cache as _c
        return _c if _c.using_redis() else None
    except Exception:
        return None


def cached_json(cache_key: str, producer, ttl_seconds: float = _CACHE_TTL_SECONDS):
    """Generic SHARED JSON cache (Redis when configured, else on-disk). On a miss
    (or expired entry) calls `producer()` and caches its result if truthy — never
    pins a transient failure.

    `cache_key` must identify the CONTENT (e.g. a game), NOT the user — so many
    users requesting the same thing collapse to one upstream call.

    Redis backing (23.6b) is what CLAUDE.md's original note called for: the local
    disk cache dies with every container, so each deploy re-bought every Tavily
    search and every appdetails lookup. Redis makes the cache shared across
    instances AND durable across deploys; disk stays as the dev/eval fallback."""
    digest = hashlib.sha1(cache_key.encode("utf-8")).hexdigest()
    r = _redis()
    if r is not None:
        try:
            raw = r.get(f"pg:{digest}")
            if raw:
                return json.loads(raw)
        except Exception:
            pass  # cache down → fall through to disk / producer

    path = _CACHE_DIR / f"{digest}.json"
    try:
        if path.exists() and (time.time() - path.stat().st_mtime) < ttl_seconds:
            value = json.loads(path.read_text("utf-8"))
            if r is not None:                      # warm Redis from the disk copy
                try:
                    r.set(f"pg:{digest}", json.dumps(value), int(ttl_seconds))
                except Exception:
                    pass
            return value
    except Exception:
        pass

    value = producer()
    if value:
        if r is not None:
            try:
                r.set(f"pg:{digest}", json.dumps(value), int(ttl_seconds))
            except Exception:
                pass
        try:
            _CACHE_DIR.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(value), "utf-8")
        except Exception:
            pass
    return value


def cache_get(cache_key: str, ttl_seconds: float = _CACHE_TTL_SECONDS):
    """Read-only lookup in the shared cache (Redis, else disk). None on a miss."""
    sentinel = object()
    out = cached_json(cache_key, lambda: sentinel, ttl_seconds)
    return None if out is sentinel else out


def cache_put(cache_key: str, value, ttl_seconds: float = _CACHE_TTL_SECONDS) -> None:
    """Write into the shared cache (both layers). Used where the value is built
    INCREMENTALLY — e.g. roadmap phase tags, which accumulate per game across
    users instead of being produced by one call (23.6b)."""
    if not value:
        return
    digest = hashlib.sha1(cache_key.encode("utf-8")).hexdigest()
    r = _redis()
    if r is not None:
        try:
            r.set(f"pg:{digest}", json.dumps(value), int(ttl_seconds))
        except Exception:
            pass
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        (_CACHE_DIR / f"{digest}.json").write_text(json.dumps(value), "utf-8")
    except Exception:
        pass


def cached_search(cache_key: str, query: str, max_results: int = 3) -> list[dict]:
    """web_search() through the shared cache (see cached_json)."""
    return cached_json(cache_key, lambda: web_search(query, max_results=max_results)) or []


def search_available() -> bool:
    """True if a Tavily key is configured (so the agent can route to how-to)."""
    return bool(TAVILY_API_KEY)
