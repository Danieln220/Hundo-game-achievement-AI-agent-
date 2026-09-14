"""Pure snapshot-directory → DataFrames loader.

Deliberately dependency-free: this module imports ONLY json, pathlib and pandas —
no config, no dotenv, no storage, no Steam client. That is what lets the sandbox
runner (agent/_sandbox_runner.py) load a user's frames under a SCRUBBED
environment without ever touching secrets or object storage (Step 23.1). The
parent process hydrates the local snapshot dir first; the child only reads it.

snapshot.load_frames() delegates here, so the fixed 3-frame schema has exactly
one implementation:
    games          -> appid, name, playtime
    achievements   -> appid, api_name, display_name, description, rarity_pct, hidden
    player_unlocks -> appid, api_name, achieved, unlock_time
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

GAMES_COLUMNS = ["appid", "name", "playtime"]
ACHIEVEMENTS_COLUMNS = ["appid", "api_name", "display_name", "description", "rarity_pct", "hidden"]
PLAYER_UNLOCKS_COLUMNS = ["appid", "api_name", "achieved", "unlock_time"]


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def frames_from_dir(snap: Path | str) -> dict[str, pd.DataFrame]:
    """Build the three frames from the four snapshot JSON files in `snap`.
    Missing files yield empty, correctly-typed frames (explicit columns — an
    empty library must not produce a column-less DataFrame)."""
    snap = Path(snap)
    raw_owned = _read_json(snap / "owned_games.json")
    games_list = raw_owned.get("response", {}).get("games", [])

    schemas = _read_json(snap / "schemas.json")
    ach_data = _read_json(snap / "achievements.json")
    pct_data = _read_json(snap / "global_pct.json")

    games_df = pd.DataFrame(
        [
            {
                "appid":    g["appid"],
                "name":     g.get("name", str(g["appid"])),
                "playtime": g.get("playtime_forever", 0),
            }
            for g in games_list
        ],
        columns=GAMES_COLUMNS,
    )

    ach_rows: list[dict] = []
    unlock_rows: list[dict] = []

    for game in games_list:
        appid = game["appid"]
        appid_str = str(appid)

        ach_list = (
            schemas.get(appid_str, {})
                   .get("game", {})
                   .get("availableGameStats", {})
                   .get("achievements", [])
        )

        pct_map = {
            a["name"]: a["percent"]
            for a in pct_data.get(appid_str, {})
                             .get("achievementpercentages", {})
                             .get("achievements", [])
        }

        for a in ach_list:
            ach_rows.append({
                "appid":        appid,
                "api_name":     a.get("name", ""),
                "display_name": a.get("displayName", ""),
                "description":  a.get("description", ""),
                "rarity_pct":   pct_map.get(a.get("name", ""), None),
                "hidden":       bool(a.get("hidden", 0)),
            })

        player_list = (
            ach_data.get(appid_str, {})
                    .get("playerstats", {})
                    .get("achievements", [])
        )

        for a in player_list:
            unlock_rows.append({
                "appid":       appid,
                "api_name":    a.get("apiname", ""),
                "achieved":    bool(a.get("achieved", 0)),
                "unlock_time": a.get("unlocktime", 0),
            })

    achievements_df = pd.DataFrame(ach_rows, columns=ACHIEVEMENTS_COLUMNS)
    achievements_df["rarity_pct"] = pd.to_numeric(achievements_df["rarity_pct"], errors="coerce")
    player_unlocks_df = pd.DataFrame(unlock_rows, columns=PLAYER_UNLOCKS_COLUMNS)

    return {
        "games":          games_df,
        "achievements":   achievements_df,
        "player_unlocks": player_unlocks_df,
    }
