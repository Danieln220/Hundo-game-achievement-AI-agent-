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


def cached_json(cache_key: str, producer, ttl_seconds: float = _CACHE_TTL_SECONDS):
    """Generic SHARED on-disk JSON cache. On a miss (or expired entry) calls
    `producer()` and caches its result if truthy (never pins a transient failure).

    `cache_key` must identify the CONTENT (e.g. a game), NOT the user — so many
    users requesting the same thing collapse to one upstream call. Multi-user note:
    swap the backing store to Redis at deploy behind this same interface — see
    CLAUDE.md "Multi-user / scaling architecture"."""
    path = _CACHE_DIR / f"{hashlib.sha1(cache_key.encode('utf-8')).hexdigest()}.json"
    try:
        if path.exists() and (time.time() - path.stat().st_mtime) < ttl_seconds:
            return json.loads(path.read_text("utf-8"))
    except Exception:
        pass

    value = producer()
    if value:
        try:
            _CACHE_DIR.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(value), "utf-8")
        except Exception:
            pass
    return value


def cached_search(cache_key: str, query: str, max_results: int = 3) -> list[dict]:
    """web_search() through the shared cache (see cached_json)."""
    return cached_json(cache_key, lambda: web_search(query, max_results=max_results)) or []


def search_available() -> bool:
    """True if a Tavily key is configured (so the agent can route to how-to)."""
    return bool(TAVILY_API_KEY)
