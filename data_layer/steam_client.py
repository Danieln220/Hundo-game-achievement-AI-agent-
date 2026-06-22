"""Thin wrappers over the free, official Steam Web API.
Each returns the parsed JSON. Key comes from config.STEAM_API_KEY."""
from typing import Optional

import requests
import config

BASE = "https://api.steampowered.com"


def resolve_vanity_url(vanity: str) -> Optional[str]:
    """ISteamUser/ResolveVanityURL — turn a custom URL name (the 'name' in
    steamcommunity.com/id/<name>) into a numeric SteamID64.
    Returns None if Steam has no match for that name."""
    resp = requests.get(
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
    resp = requests.get(
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
    resp = requests.get(
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
    resp = requests.get(
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
    resp = requests.get(
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


def get_global_achievement_pct(appid: int) -> dict:
    """ISteamUserStats/GetGlobalAchievementPercentagesForApp — rarity %."""
    resp = requests.get(
        f"{BASE}/ISteamUserStats/GetGlobalAchievementPercentagesForApp/v2/",
        params={
            "gameid": appid,
            "format": "json",
        },
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()
