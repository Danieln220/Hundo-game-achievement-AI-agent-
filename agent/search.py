"""Thin web-search wrapper over the Tavily API (used for achievement how-to
guides). Calls the REST endpoint directly with `requests` — no extra SDK, and
it picks up the truststore SSL fix from config like the Steam client does.

Returns a small, normalized list of {title, url, content} dicts so the agent
can synthesize an answer with citations. Network/credential failures degrade
to an empty list rather than crashing a run.
"""
from typing import Optional

import requests

from config import TAVILY_API_KEY

_ENDPOINT = "https://api.tavily.com/search"


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


def search_available() -> bool:
    """True if a Tavily key is configured (so the agent can route to how-to)."""
    return bool(TAVILY_API_KEY)
