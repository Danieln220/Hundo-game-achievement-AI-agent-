"""23.6a — A/B the two owned-roadmap engines on REAL snapshot data.

The deterministic engine must reproduce what the code-gen prompt asks the model
to produce: the same locked achievements, same easiest-first order, same totals.
Run: PYTHONPATH=. python eval/roadmap_check.py [steam_id]
"""
import sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
from config import STEAM_ID
from data_layer.snapshot import load_frames
from agent.graph import _owned_roadmap_data, _match_owned_game

SID = sys.argv[1] if len(sys.argv) > 1 else STEAM_ID
frames = load_frames(SID)
games = frames["games"]

_results = []
def check(label, ok, detail=""):
    _results.append(bool(ok))
    print(f"{'PASS' if ok else 'FAIL'}  {label}" + (f"\n      {detail}" if detail and not ok else ""))


def reference(appid: int, name: str) -> dict:
    """Independent re-implementation of the code-gen CONTRACT (_ROADMAP_CODE_SYSTEM),
    written from the prompt rather than from _owned_roadmap_data, so agreement
    between the two is real evidence and not a copy of the same mistake."""
    ach, pu = frames["achievements"], frames["player_unlocks"]
    mine = ach[ach.appid == appid]
    got = pu[(pu.appid == appid) & (pu.achieved == True)]          # noqa: E712
    locked = mine[~mine.api_name.isin(set(got.api_name))].copy()
    locked["r"] = pd.to_numeric(locked.rarity_pct, errors="coerce")
    locked = locked.sort_values("r", ascending=False, na_position="last")
    return {
        "target": name,
        "total": len(mine),
        "unlocked": len(mine) - len(locked),
        "remaining": [
            {"name": str(r.display_name or r.api_name),
             "rarity_pct": None if pd.isna(r.r) else float(r.r)}
            for r in locked.head(60).itertuples()
        ],
    }


# Pick the games with the most achievements — the interesting, non-trivial cases.
counts = (frames["achievements"].groupby("appid").size().sort_values(ascending=False))
appids = [a for a in counts.index[:8]]
name_by_appid = dict(zip(games.appid, games.name))

print(f"profile {SID} — {len(games)} games\n")
for appid in appids:
    name = str(name_by_appid.get(appid, appid))
    want = reference(appid, name)
    t0 = time.perf_counter()
    got = _owned_roadmap_data(appid, name, frames)
    ms = (time.perf_counter() - t0) * 1000

    same_counts = (got["total"], got["unlocked"]) == (want["total"], want["unlocked"])
    check(f"{name[:34]:34} totals {got['unlocked']}/{got['total']} ({ms:.0f}ms)", same_counts,
          f"got {(got['total'], got['unlocked'])} want {(want['total'], want['unlocked'])}")
    check(f"{'':34} same locked list + order",
          [a["name"] for a in got["remaining"]] == [a["name"] for a in want["remaining"]],
          f"{[a['name'] for a in got['remaining']][:3]} vs {[a['name'] for a in want['remaining']][:3]}")
    rar = [a["rarity_pct"] for a in got["remaining"] if a["rarity_pct"] is not None]
    check(f"{'':34} easiest-first (rarity descending)", rar == sorted(rar, reverse=True))
    check(f"{'':34} capped at 60", len(got["remaining"]) <= 60, len(got["remaining"]))
    check(f"{'':34} fields JSON-safe",
          all(isinstance(a["name"], str) and isinstance(a["hidden"], bool)
              and (a["rarity_pct"] is None or isinstance(a["rarity_pct"], float))
              for a in got["remaining"]))

# a fully-completed game reports nothing remaining
done = [a for a in appids if _owned_roadmap_data(a, str(name_by_appid.get(a, a)), frames)["unlocked"]
        == _owned_roadmap_data(a, str(name_by_appid.get(a, a)), frames)["total"]]
print(f"\n(perfect games among the sample: {len(done)})")

# the resolver + engine agree on what the user typed
hit = _match_owned_game("build me a roadmap to 100% " + str(name_by_appid.get(appids[0], "")), games)
check("named game resolves to an owned appid", hit is not None and hit[1] == appids[0], str(hit))

# ── category filters resolve from the SAME tags the phase view shows ─────────
from agent.graph import _requested_categories, _filter_by_category, _category_of

for q, want_ex, want_in in [
    ("roadmap to 100% Forza Horizon 4, skip multiplayer", {"multiplayer"}, set()),
    ("roadmap but no DLC", {"dlc"}, set()),
    ("plan my 100% without grinding", {"grind"}, set()),
    ("skip online and no expansion stuff", {"multiplayer", "dlc"}, set()),
    ("only multiplayer achievements", set(), {"multiplayer"}),
    ("build me a roadmap", set(), set()),
    ("only the easy ones", set(), set()),          # difficulty is handled elsewhere
]:
    got = _requested_categories(q)
    check(f"parse {q[:38]!r:42}", got == (want_ex, want_in), f"got {got}")

ITEMS = [
    {"name": "Win online", "category": "multiplayer"},
    {"name": "Finish story", "category": "story"},
    {"name": "Buy the DLC car", "category": "dlc"},
    {"name": "Grind 500 laps", "category": "grind"},
    {"name": "Untagged co-op race", "description": "Win a co-op event"},   # no tag → keywords
]
out = _filter_by_category(ITEMS, {"multiplayer"}, set())
check("excluding a category drops BOTH tagged and untagged matches",
      [a["name"] for a in out] == ["Finish story", "Buy the DLC car", "Grind 500 laps"],
      [a["name"] for a in out])
out = _filter_by_category(ITEMS, set(), {"multiplayer"})
check("'only X' keeps just that category",
      [a["name"] for a in out] == ["Win online", "Untagged co-op race"], [a["name"] for a in out])
check("a filter that would empty the list falls back to everything",
      _filter_by_category(ITEMS, {"multiplayer", "story", "dlc", "grind"}, set()) == ITEMS)
check("untagged item falls back to keyword category",
      _category_of({"name": "Untagged co-op race", "description": "Win a co-op event"}) == "multiplayer")
check("unmatched untagged item has no category", _category_of({"name": "Take a photo"}) is None)

# ── 23.6d/e: the in-process caches ───────────────────────────────────────────
import agent as _agent
from data_layer.snapshot import load_frames as _lf
f1, f2 = _lf(SID), _lf(SID)
check("frames are cached in-process (same object)", f1 is f2)
check("compiled graph is cached per user+snapshot",
      _agent._graph_for(SID, f1) is _agent._graph_for(SID, f1))

print()
p, t = sum(_results), len(_results)
print(f"  {p} passed, {t - p} failed")
sys.exit(0 if p == t else 1)
