"""Hundo live watch mode — a standalone companion that auto-follows whatever
game you're playing and reacts to new achievement unlocks in real time.

Run:
    py -3.11 watch.py <steam_id>      # defaults to env STEAM_ID
    py -3.11 watch.py <steam_id> --interval 60 --no-llm

How it stays cheap: each cycle it asks GetPlayerSummaries (1 call) what game you
are in RIGHT NOW, then polls only THAT game's achievements (1 call). ~2 calls per
cycle regardless of library size. Polling current game's achievements is the one
sanctioned exception to "always use the snapshot" — watching needs live data.

Honest limits: polling means ~30-60s latency (Steam has no unlock push events),
and this is a console/notification companion, NOT an in-game overlay (Steam does
not open its overlay to third parties).
"""
from __future__ import annotations

import time
from typing import Optional

import pandas as pd

from config import STEAM_ID, DEEPSEEK_MODEL_FLASH
from data_layer import steam_client
from data_layer.snapshot import load_frames
from agent.llm import call_llm

_FLAIR_SYSTEM = (
    "You are a hype gaming buddy. The user just unlocked a Steam achievement. "
    "React with ONE short, fun, encouraging sentence. No stats — just vibes."
)


# ── Pure helpers (unit-testable, no network) ──────────────────────────────────

def unlocked_from_response(resp: dict) -> set[str]:
    """Set of api_names the player has achieved, from a GetPlayerAchievements response."""
    achs = resp.get("playerstats", {}).get("achievements", [])
    return {a.get("apiname", "") for a in achs if a.get("achieved", 0)}


def diff_unlocks(prev: set[str], now: set[str]) -> list[str]:
    """Newly unlocked api_names (in `now` but not `prev`)."""
    return sorted(now - prev)


def game_meta(frames: dict[str, pd.DataFrame], appid: int):
    """(game_name, {api_name: (display_name, rarity_pct|None)}, total_achievements)
    for a game, pulled from the snapshot. Empty/zero if the game isn't cached."""
    games, ach = frames["games"], frames["achievements"]
    name_row = games[games["appid"] == appid]
    name = name_row["name"].iloc[0] if not name_row.empty else str(appid)
    sub = ach[ach["appid"] == appid]
    ach_map = {
        r.api_name: (r.display_name, None if pd.isna(r.rarity_pct) else float(r.rarity_pct))
        for r in sub.itertuples()
    }
    return name, ach_map, len(sub)


def make_commentary(api_name: str, meta, unlocked_count: int, use_llm: bool = False) -> str:
    """Grounded one-line reaction to a new unlock. Degrades gracefully when the
    game/achievement isn't in the snapshot."""
    name, ach_map, total = meta
    display, rarity = ach_map.get(api_name, (api_name, None))

    line = f"🎉 Unlocked '{display}'"
    if rarity is not None:
        line += (f" — only {rarity:.1f}% of players have it!"
                 if rarity < 10 else f" ({rarity:.1f}% rarity)")
    if total:
        remaining = max(total - unlocked_count, 0)
        line += (f" · 100% complete in {name}! 🏆" if remaining == 0
                 else f" · {remaining} left in {name}")

    if use_llm:
        try:
            flair = call_llm(f"Achievement: {display}",
                             model=DEEPSEEK_MODEL_FLASH, system=_FLAIR_SYSTEM).strip()
            if flair:
                line += f"\n   {flair}"
        except Exception:
            pass
    return line


# ── Notifications ─────────────────────────────────────────────────────────────

def _toast(title: str, message: str) -> None:
    """Optional desktop toast. Import-guarded so a missing lib never breaks watch."""
    try:
        from plyer import notification  # type: ignore
        notification.notify(title=title, message=message, timeout=5)
    except Exception:
        pass


def notify(message: str) -> None:
    print(message, flush=True)
    _toast("Hundo", message.split("\n")[0][:200])


# ── Watch loop ────────────────────────────────────────────────────────────────

def current_game(steam_id: str) -> Optional[tuple[int, str]]:
    """(appid, name) of the game the user is in right now, or None if not in-game."""
    player = steam_client.get_player_summary(steam_id)
    gameid = player.get("gameid")
    if not gameid:
        return None
    try:
        appid = int(gameid)
    except (TypeError, ValueError):
        return None
    return appid, player.get("gameextrainfo", str(appid))


def watch(steam_id: str = STEAM_ID, interval: int = 45, use_llm: bool = True) -> None:
    frames = load_frames(steam_id)
    seen: dict[int, set[str]] = {}
    active: Optional[int] = None
    backoff = interval

    print(f"👀 Watching {steam_id} — auto-follows your current game. Ctrl+C to stop.\n", flush=True)

    while True:
        try:
            game = current_game(steam_id)

            if game is None:
                if active is not None:
                    print("· no longer in a game (idling)", flush=True)
                    active = None
                time.sleep(interval)
                continue

            appid, name = game
            resp = steam_client.get_player_achievements(steam_id, appid)
            now = unlocked_from_response(resp)

            if appid != active:
                # Just started this game: snapshot the current state, react only to
                # unlocks that happen FROM NOW (don't replay everything already earned).
                print(f"▶ Now playing: {name} ({appid}) — {len(now)} already unlocked", flush=True)
                active = appid
                seen[appid] = now
            else:
                new = diff_unlocks(seen.get(appid, set()), now)
                if new:
                    meta = game_meta(frames, appid)
                    for api in new:
                        notify(make_commentary(api, meta, len(now), use_llm))
                    seen[appid] = now

            backoff = interval
            time.sleep(interval)

        except KeyboardInterrupt:
            print("\n👋 Stopped watching.", flush=True)
            break
        except Exception as exc:  # network hiccup / rate limit → back off, keep going
            print(f"· error: {exc} (backing off {backoff}s)", flush=True)
            time.sleep(backoff)
            backoff = min(backoff * 2, 300)


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser(description="Hundo live watch mode")
    p.add_argument("steam_id", nargs="?", default=STEAM_ID,
                   help="SteamID64 to watch (default: env STEAM_ID)")
    p.add_argument("--interval", type=int, default=45, help="seconds between polls")
    p.add_argument("--no-llm", action="store_true", help="disable the LLM flair line")
    args = p.parse_args()

    watch(args.steam_id, interval=args.interval, use_llm=not args.no_llm)
