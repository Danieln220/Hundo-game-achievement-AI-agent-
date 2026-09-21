"""Thin wrappers over the free, official Steam Web API.
Each returns the parsed JSON. Key comes from config.STEAM_API_KEY."""
from typing import Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

import config

BASE = "https://api.steampowered.com"

# One shared, retrying Session for every Steam call (23.3a). Without it a single
# transient 429/5xx during a snapshot build was swallowed by _fetch_one and frozen
# into a "ready" snapshot as an empty game. Retry HONORS Retry-After on 429s.
# pool sizes >= snapshot._FETCH_WORKERS so the 16 build threads don't contend.
_RETRY = Retry(
    total=3,
    backoff_factor=0.5,                       # 0.5s, 1s, 2s
    status_forcelist=(429, 500, 502, 503, 504),
    allowed_methods=("GET",),
    respect_retry_after_header=True,
    raise_on_status=True,                     # exhausted retries -> RetryError
)
_SESSION = requests.Session()
_SESSION.mount("https://", HTTPAdapter(max_retries=_RETRY, pool_connections=16, pool_maxsize=32))
_SESSION.mount("http://", HTTPAdapter(max_retries=_RETRY, pool_connections=16, pool_maxsize=32))


def resolve_vanity_url(vanity: str) -> Optional[str]:
    """ISteamUser/ResolveVanityURL — turn a custom URL name (the 'name' in
    steamcommunity.com/id/<name>) into a numeric SteamID64.
    Returns None if Steam has no match for that name."""
    resp = _SESSION.get(
        f"{BASE}/ISteamUser/ResolveVanityURL/v1/",
        params={
            "key": config.STEAM_API_KEY,
            "vanityurl": vanity,
            "format": "json",
        },
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json().get("response", {})
    if data.get("success") == 1:
        return data.get("steamid")
    return None


def get_player_summary(steam_id: str) -> dict:
    """ISteamUser/GetPlayerSummaries — basic profile. When the user is currently
    in a game (and their profile is public) the player object includes
    `gameid` and `gameextrainfo`. Used by watch mode to auto-detect the active
    game. Returns the single player dict (or {} if unavailable)."""
    resp = _SESSION.get(
        f"{BASE}/ISteamUser/GetPlayerSummaries/v0002/",
        params={
            "key": config.STEAM_API_KEY,
            "steamids": steam_id,
            "format": "json",
        },
        timeout=10,
    )
    resp.raise_for_status()
    players = resp.json().get("response", {}).get("players", [])
    return players[0] if players else {}


def get_owned_games(steam_id: str) -> dict:
    """IPlayerService/GetOwnedGames — games + playtime + app info."""
    resp = _SESSION.get(
        f"{BASE}/IPlayerService/GetOwnedGames/v0001/",
        params={
            "key": config.STEAM_API_KEY,
            "steamid": steam_id,
            "include_appinfo": 1,
            "include_played_free_games": 1,
            "format": "json",
        },
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


def get_schema_for_game(appid: int) -> dict:
    """ISteamUserStats/GetSchemaForGame — full achievement list (name,
    displayName, description, hidden)."""
    resp = _SESSION.get(
        f"{BASE}/ISteamUserStats/GetSchemaForGame/v2/",
        params={
            "key": config.STEAM_API_KEY,
            "appid": appid,
            "format": "json",
        },
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


def get_player_achievements(steam_id: str, appid: int) -> dict:
    """ISteamUserStats/GetPlayerAchievements — unlocked flags + timestamps.
    Returns empty dict if the game has no stats page (not a crash)."""
    resp = _SESSION.get(
        f"{BASE}/ISteamUserStats/GetPlayerAchievements/v1/",
        params={
            "key": config.STEAM_API_KEY,
            "steamid": steam_id,
            "appid": appid,
            "format": "json",
        },
        timeout=10,
    )
    if resp.status_code == 400:
        return {}
    resp.raise_for_status()
    data = resp.json()
    if not data.get("playerstats", {}).get("success", True):
        return {}
    return data


def search_app(term: str) -> list[dict]:
    """Steam storefront search — resolve a game NAME to ranked [{appid, name}].
    Official store endpoint; works for ANY game (owned or not). Used to build a
    roadmap for a game the player doesn't own. Returns [] on failure."""
    try:
        resp = _SESSION.get(
            "https://store.steampowered.com/api/storesearch/",
            params={"term": term, "cc": "us", "l": "en"},
            timeout=10,
        )
        resp.raise_for_status()
        items = resp.json().get("items", [])
    except Exception:
        return []
    return [{"appid": it.get("id"), "name": it.get("name", "")}
            for it in items if it.get("id")]


def get_global_achievement_pct(appid: int) -> dict:
    """ISteamUserStats/GetGlobalAchievementPercentagesForApp — rarity %.

    Steam answers 403/400 for apps that simply have NO global stats (most games
    without achievements). That's an empty result, not a failure — returning {}
    keeps it out of the build's failure count, which would otherwise trip the
    incompleteness threshold on a library of achievement-less games (23.3a)."""
    resp = _SESSION.get(
        f"{BASE}/ISteamUserStats/GetGlobalAchievementPercentagesForApp/v2/",
        params={
            "gameid": appid,
            "format": "json",
        },
        timeout=10,
    )
    if resp.status_code in (400, 403):
        return {}
    resp.raise_for_status()
    return resp.json()


def get_most_played() -> list[int]:
    """ISteamChartsService/GetMostPlayedGames — currently most-played games on Steam
    (public, no key). Returns ranked appids. Used to surface 'popular right now'."""
    resp = _SESSION.get(f"{BASE}/ISteamChartsService/GetMostPlayedGames/v1/", timeout=10)
    resp.raise_for_status()
    ranks = resp.json().get("response", {}).get("ranks", [])
    return [r["appid"] for r in ranks if r.get("appid")]


def get_app_details(appid: int) -> dict:
    """Steam storefront appdetails (basic) for one app → {name, type}. {} on failure
    or non-success. Used to resolve names for the 'popular right now' picks."""
    resp = _SESSION.get(
        "https://store.steampowered.com/api/appdetails",
        params={"appids": appid, "filters": "basic"},
        timeout=10,
    )
    resp.raise_for_status()
    entry = resp.json().get(str(appid), {})
    if not entry.get("success"):
        return {}
    d = entry.get("data", {})
    return {"name": d.get("name", ""), "type": d.get("type", "")}
