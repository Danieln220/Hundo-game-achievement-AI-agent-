"""Frame-contract checks (columns AND dtypes, empty or not) + the consumers that
broke on untyped empty frames (2026-09-15: HTTP 500 on zero-unlock profiles).
Offline, no network, no LLM.  Run:  py eval/frames_check.py
"""
import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

from data_layer.frames import (  # noqa: E402
    frames_from_dir, GAMES_DTYPES, ACHIEVEMENTS_DTYPES, PLAYER_UNLOCKS_DTYPES,
)
from data_layer.library import header_stats  # noqa: E402

_results: list[bool] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    _results.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {label}" + (f"\n      {detail}" if detail and not ok else ""))


def dtypes_ok(frames) -> tuple[bool, str]:
    want = {"games": GAMES_DTYPES, "achievements": ACHIEVEMENTS_DTYPES, "player_unlocks": PLAYER_UNLOCKS_DTYPES}
    for name, exp in want.items():
        got = dict(frames[name].dtypes.astype(str))
        if list(got) != list(exp):
            return False, f"{name} columns {list(got)} != {list(exp)}"
        for col, dt in exp.items():
            g = got[col]
            # 'str' is pandas 3's string dtype; accept its spellings.
            if dt == "str" and g in ("str", "string", "object"):
                continue
            if g != dt:
                return False, f"{name}.{col} dtype {g} != {dt}"
    return True, ""


tmp = Path(tempfile.mkdtemp(prefix="hundo_frames_"))
try:
    # ── 1. completely empty directory (no snapshot at all) ───────────────────
    f = frames_from_dir(tmp / "missing")
    ok, why = dtypes_ok(f)
    check("empty dir: contract columns + dtypes", ok, why)
    pu = f["player_unlocks"]
    check("empty dir: boolean mask keeps columns (not a column selection)",
          list(pu[pu["achieved"]].columns) == list(PLAYER_UNLOCKS_DTYPES))
    check("empty dir: header_stats works", header_stats(f) == {"games": 0, "unlocked": 0, "total": 0, "perfect": 0},
          str(header_stats(f)))

    # ── 2. games but ZERO achievements/unlocks (the live 500 case) ───────────
    d = tmp / "no_ach"
    d.mkdir()
    (d / "owned_games.json").write_text(json.dumps({"response": {"game_count": 2, "games": [
        {"appid": 1, "name": "Game A", "playtime_forever": 30},
        {"appid": 2, "name": "Game B", "playtime_forever": 0},
    ]}}))
    for name in ("schemas.json", "achievements.json", "global_pct.json"):
        (d / name).write_text("{}")
    f = frames_from_dir(d)
    ok, why = dtypes_ok(f)
    check("no-achievement library: contract columns + dtypes", ok, why)
    check("no-achievement library: header_stats == 2 games / 0 / 0 / 0",
          header_stats(f) == {"games": 2, "unlocked": 0, "total": 0, "perfect": 0}, str(header_stats(f)))
    merged = f["achievements"].merge(f["player_unlocks"][["appid", "api_name", "achieved", "unlock_time"]],
                                     on=["appid", "api_name"], how="left")
    check("no-achievement library: library-style merge works", list(merged.columns)[:2] == ["appid", "api_name"])

    # ── 3. a normal tiny snapshot keeps its natural dtypes + values ──────────
    d2 = tmp / "tiny"
    d2.mkdir()
    (d2 / "owned_games.json").write_text(json.dumps({"response": {"games": [{"appid": 7, "name": "G", "playtime_forever": 120}]}}))
    (d2 / "schemas.json").write_text(json.dumps({"7": {"game": {"availableGameStats": {"achievements": [
        {"name": "A1", "displayName": "First", "description": "d", "hidden": 0},
        {"name": "A2", "displayName": "Second", "description": "", "hidden": 1},
    ]}}}}))
    (d2 / "achievements.json").write_text(json.dumps({"7": {"playerstats": {"achievements": [
        {"apiname": "A1", "achieved": 1, "unlocktime": 1700000000},
        {"apiname": "A2", "achieved": 0, "unlocktime": 0},
    ]}}}))
    (d2 / "global_pct.json").write_text(json.dumps({"7": {"achievementpercentages": {"achievements": [
        {"name": "A1", "percent": "61.5"}, {"name": "A2", "percent": 3.25},
    ]}}}))
    f = frames_from_dir(d2)
    ok, why = dtypes_ok(f)
    check("tiny snapshot: contract columns + dtypes", ok, why)
    ach = f["achievements"].set_index("api_name")
    check("tiny snapshot: rarity parsed from string AND number",
          abs(ach.loc["A1", "rarity_pct"] - 61.5) < 1e-9 and abs(ach.loc["A2", "rarity_pct"] - 3.25) < 1e-9)
    check("tiny snapshot: hidden is bool", bool(ach.loc["A2", "hidden"]) is True and bool(ach.loc["A1", "hidden"]) is False)
    check("tiny snapshot: header_stats", header_stats(f) == {"games": 1, "unlocked": 1, "total": 2, "perfect": 0}, str(header_stats(f)))
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print()
passed, total = sum(_results), len(_results)
print(f"  {passed} passed, {total - passed} failed")
sys.exit(0 if passed == total else 1)
