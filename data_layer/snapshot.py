"""Fetch a user's Steam data, cache to JSON, and load it into DataFrames.

Multi-user: each user's snapshot lives in its own dir, keyed by SteamID64:
    data/snapshot/<steam_id>/owned_games.json
    data/snapshot/<steam_id>/schemas.json       {appid: GetSchemaForGame}
    data/snapshot/<steam_id>/achievements.json  {appid: GetPlayerAchievements}
    data/snapshot/<steam_id>/global_pct.json    {appid: GetGlobalAchievementPercentages}

Build against a frozen snapshot: reproducible runs, stable eval, no API hammering.
A legacy flat layout (files directly under data/snapshot/) is still honoured for
the default STEAM_ID so the existing eval keeps working untouched.
"""
import json
import shutil
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd

from config import SNAPSHOT_DIR, STEAM_ID
from . import steam_client

# Bounded so we fetch fast without tripping Steam's rate limits. Each game needs
# 3 calls, so a big library = hundreds of calls; more workers = shorter wall time.
_FETCH_WORKERS = 16

# A snapshot older than this is considered stale and rebuilt by ensure_snapshot
# when a max_age is requested. Keeps cached data fresh without piling up forever.
DEFAULT_MAX_AGE_DAYS = 7


class PrivateProfileError(RuntimeError):
    """Raised when a profile is private / friends-only and exposes no games."""


def _user_dir(steam_id: str) -> Path:
    return Path(SNAPSHOT_DIR) / str(steam_id)


def _resolve_snapshot_dir(steam_id: str) -> Path:
    """Where THIS user's snapshot lives. Prefer the per-user dir; fall back to
    the legacy flat layout for the configured default user."""
    user_dir = _user_dir(steam_id)
    if user_dir.exists():
        return user_dir
    base = Path(SNAPSHOT_DIR)
    if str(steam_id) == str(STEAM_ID) and (base / "owned_games.json").exists():
        return base  # legacy flat snapshot
    return user_dir  # may not exist yet → load_frames returns empty frames


def has_snapshot(steam_id: str = STEAM_ID) -> bool:
    """True if a usable snapshot already exists for this user."""
    return (_resolve_snapshot_dir(steam_id) / "owned_games.json").exists()


def snapshot_age_days(steam_id: str = STEAM_ID) -> float | None:
    """Age of this user's snapshot in days, or None if there is none."""
    marker = _resolve_snapshot_dir(steam_id) / "owned_games.json"
    if not marker.exists():
        return None
    return (time.time() - marker.stat().st_mtime) / 86400


def clear_snapshot(steam_id: str = STEAM_ID) -> bool:
    """Delete a user's per-user snapshot dir. Returns True if something was removed.
    Never touches the legacy flat layout (which holds the default user's eval data)."""
    user_dir = _user_dir(steam_id)
    if user_dir.exists():
        shutil.rmtree(user_dir)
        return True
    return False


def _fetch_one(kind: str, steam_id: str, appid: int) -> tuple[str, int, dict]:
    """Fetch a single endpoint for one game. Tagged by `kind` so results can be
    regrouped after running through the thread pool. Failures degrade to {}."""
    try:
        if kind == "schema":
            return kind, appid, steam_client.get_schema_for_game(appid)
        if kind == "ach":
            return kind, appid, steam_client.get_player_achievements(steam_id, appid)
        if kind == "pct":
            return kind, appid, steam_client.get_global_achievement_pct(appid)
    except Exception:
        pass
    return kind, appid, {}


def build_snapshot(steam_id: str = STEAM_ID, progress_cb=None) -> None:
    """Fetch one user's Steam data concurrently and write 4 JSON files into
    data/snapshot/<steam_id>/. Raises PrivateProfileError if the profile is private.

    `progress_cb(done, total)` is called as calls complete, so a UI can show a
    live progress bar (total = number of games * 3 endpoints)."""
    steam_id = str(steam_id)
    out = _user_dir(steam_id)
    out.mkdir(parents=True, exist_ok=True)

    print(f"Fetching owned games for {steam_id}...")
    owned = steam_client.get_owned_games(steam_id)
    games = owned.get("response", {}).get("games")

    # A private/friends-only profile returns an empty response with no "games" key.
    if games is None:
        raise PrivateProfileError(
            "This Steam profile is private. Set 'Game details' to Public in "
            "Steam → Profile → Privacy Settings, then try again."
        )

    (out / "owned_games.json").write_text(json.dumps(owned, indent=2))
    print(f"Found {len(games)} games. Fetching per-game data ({_FETCH_WORKERS} workers)...")

    # Flatten every (game, endpoint) into an independent unit so all calls — not
    # just one per game — share the worker pool. Big libraries finish far sooner.
    appids = [g["appid"] for g in games]
    schemas      = {a: {} for a in appids}
    achievements = {a: {} for a in appids}
    global_pct   = {a: {} for a in appids}
    buckets = {"schema": schemas, "ach": achievements, "pct": global_pct}

    work = [(kind, a) for a in appids for kind in ("schema", "ach", "pct")]
    total = len(work)
    done = 0

    with ThreadPoolExecutor(max_workers=_FETCH_WORKERS) as pool:
        futures = [pool.submit(_fetch_one, kind, steam_id, a) for kind, a in work]
        for fut in as_completed(futures):
            kind, appid, data = fut.result()
            buckets[kind][appid] = data
            done += 1
            if progress_cb:
                progress_cb(done, total)
            if done % 30 == 0 or done == total:
                print(f"  {done}/{total} calls done")

    (out / "schemas.json").write_text(json.dumps(schemas, indent=2))
    (out / "achievements.json").write_text(json.dumps(achievements, indent=2))
    (out / "global_pct.json").write_text(json.dumps(global_pct, indent=2))

    print(f"Snapshot complete — {len(games)} games, 4 files in {out}/")


def ensure_snapshot(
    steam_id: str = STEAM_ID,
    max_age_days: float | None = None,
    progress_cb=None,
) -> None:
    """Build the snapshot if this user has none, or if `max_age_days` is given and
    the existing one is older than that. Otherwise reuse the cached snapshot."""
    if not has_snapshot(steam_id):
        build_snapshot(steam_id, progress_cb=progress_cb)
        return
    if max_age_days is not None:
        age = snapshot_age_days(steam_id)
        if age is not None and age > max_age_days:
            print(f"Snapshot is {age:.1f} days old (> {max_age_days}) — refreshing...")
            build_snapshot(steam_id, progress_cb=progress_cb)


def load_frames(steam_id: str = STEAM_ID) -> dict[str, pd.DataFrame]:
    """Load this user's cached snapshot into the three frames the agent expects:
        games          -> appid, name, playtime
        achievements   -> appid, api_name, display_name, description, rarity_pct, hidden
        player_unlocks -> appid, api_name, achieved, unlock_time
    Returns empty (correctly-typed) frames if no snapshot exists for the user.
    """
    snap = _resolve_snapshot_dir(str(steam_id))

    owned_path = snap / "owned_games.json"
    raw_owned = json.loads(owned_path.read_text()) if owned_path.exists() else {}
    games_list = raw_owned.get("response", {}).get("games", [])

    schemas  = json.loads((snap / "schemas.json").read_text())      if (snap / "schemas.json").exists()      else {}
    ach_data = json.loads((snap / "achievements.json").read_text())  if (snap / "achievements.json").exists()  else {}
    pct_data = json.loads((snap / "global_pct.json").read_text())    if (snap / "global_pct.json").exists()    else {}

    games_df = pd.DataFrame([
        {
            "appid":    g["appid"],
            "name":     g.get("name", str(g["appid"])),
            "playtime": g.get("playtime_forever", 0),
        }
        for g in games_list
    ])

    ach_rows = []
    unlock_rows = []

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

    achievements_df = pd.DataFrame(ach_rows) if ach_rows else pd.DataFrame(
        columns=["appid", "api_name", "display_name", "description", "rarity_pct", "hidden"]
    )
    achievements_df["rarity_pct"] = pd.to_numeric(achievements_df["rarity_pct"], errors="coerce")
    player_unlocks_df = pd.DataFrame(unlock_rows) if unlock_rows else pd.DataFrame(
        columns=["appid", "api_name", "achieved", "unlock_time"]
    )

    return {
        "games":          games_df,
        "achievements":   achievements_df,
        "player_unlocks": player_unlocks_df,
    }


if __name__ == "__main__":
    build_snapshot()
