"""Offline checks for watch.py's pure session helpers (Step 16.0) — NO network,
no LLM, runs in under a second.

Covers: chase-list ordering/filtering, recap composition across edge cases,
and the unlock-diff plumbing they build on.

Usage (from repo root):  python eval/watch_check.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from watch import build_chase_list, build_recap, diff_unlocks, unlocked_from_response

ok_count = fail_count = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global ok_count, fail_count
    status = "PASS" if cond else "FAIL"
    ok_count += int(cond)
    fail_count += int(not cond)
    print(f"{status}  {label}" + (f"  [{detail}]" if detail and not cond else ""))


# (name, {api: (display, rarity)}, total) — the shape game_meta returns.
META = ("TestGame", {
    "A": ("Easy A", 80.0),
    "B": ("Mid B", 30.0),
    "C": ("Rare C", 2.0),
    "D": ("Unknown D", None),
    "E": ("Easy E", 60.0),
    "F": ("Mid F", 25.0),
    "G": ("Done G", 90.0),
}, 7)

# ── chase list ────────────────────────────────────────────────────────────────
chase = build_chase_list(META, unlocked={"G"}, n=5)
check("chase: excludes already-unlocked", all(d != "Done G" for d, _ in chase))
check("chase: easiest first", [d for d, _ in chase] ==
      ["Easy A", "Easy E", "Mid B", "Mid F", "Rare C"], f"got {[d for d, _ in chase]}")
check("chase: capped at n", len(chase) == 5)

full = build_chase_list(META, unlocked=set(), n=10)
check("chase: unknown rarity sorts last", full[-1][0] == "Unknown D")

check("chase: empty when all unlocked",
      build_chase_list(META, unlocked=set(META[1]), n=5) == [])
check("chase: empty for un-snapshotted game",
      build_chase_list(("X", {}, 0), unlocked=set(), n=5) == [])

# ── recap ─────────────────────────────────────────────────────────────────────
sess = {"name": "TestGame", "t0": 1000.0, "pulls": [("Easy A", 80.0), ("Rare C", 2.0)]}
r = build_recap(sess, total=7, last_count=5, now_ts=1000.0 + 47 * 60)
check("recap: duration", "47 min" in r, r)
check("recap: pull count", "2 pulls" in r, r)
check("recap: rarest is the MIN rarity", "rarest 2.0%" in r, r)
check("recap: completion", "71%" in r and "(2 left)" in r, r)

one = {"name": "G", "t0": 0.0, "pulls": [("Only", 50.0)]}
check("recap: singular pull", "1 pull " in build_recap(one, 10, 3, now_ts=120) + " ")

none = {"name": "G", "t0": 0.0, "pulls": []}
r2 = build_recap(none, 10, 3, now_ts=120)
check("recap: no pulls", "no new pulls" in r2 and "rarest" not in r2, r2)

r3 = build_recap(sess, total=7, last_count=7, now_ts=4000)
check("recap: 100% celebrates", "100% complete" in r3, r3)

r4 = build_recap(sess, total=0, last_count=0, now_ts=4000)
check("recap: no completion segment without snapshot total",
      "%" not in r4.split("rarest")[-1].replace("2.0%", ""), r4)

unknown_only = {"name": "G", "t0": 0.0, "pulls": [("Mystery", None)]}
r5 = build_recap(unknown_only, 10, 3, now_ts=120)
check("recap: unknown-rarity pull skips rarest", "rarest" not in r5, r5)

check("recap: sub-minute session rounds to 1 min",
      "1 min" in build_recap(none, 0, 0, now_ts=30))

# ── diff plumbing ─────────────────────────────────────────────────────────────
check("diff: new unlocks only", diff_unlocks({"a"}, {"a", "b", "c"}) == ["b", "c"])
check("diff: nothing new", diff_unlocks({"a", "b"}, {"a", "b"}) == [])
resp = {"playerstats": {"achievements": [
    {"apiname": "x", "achieved": 1}, {"apiname": "y", "achieved": 0}]}}
check("unlocked_from_response: achieved only", unlocked_from_response(resp) == {"x"})

print(f"\n  {ok_count} passed, {fail_count} failed")
sys.exit(1 if fail_count else 0)
