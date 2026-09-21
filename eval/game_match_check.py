"""23.4a — _match_owned_game must need a WHOLE WORD, and a game CUE for
one-word titles. A miss is safe (the LLM title extractor picks it up); a false
positive silently answers about the wrong game.
Run: PYTHONPATH=. python eval/game_match_check.py"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
from agent.graph import _match_owned_game
from agent.fastpath import _scope

GAMES = pd.DataFrame({
    "appid": [1, 2, 3, 4, 5, 6, 7, 8, 9],
    "name": ["INSIDE", "Rust", "Control", "Prey", "ARK: Survival Evolved",
             "Hades", "Left 4 Dead 2", "Among Us", "Portal 2"],
})

# (question, expected owned title or None)
CASES = [
    # false positives the old bare-substring match produced
    ("am I inside the top 10 percent?", None),
    ("what's inside my library?", None),
    ("do I control my backlog?", None),
    ("prey on my easy wins", None),
    ("which games am I rustiest at?", None),
    ("is there an ark of achievements I'm missing?", None),
    ("my rarest achievement overall", None),
    ("what should I play next?", None),
    # real references still resolve
    ("build me a roadmap for INSIDE", "INSIDE"),
    ("how do I 100% Rust?", "Rust"),
    ("Rust achievements", "Rust"),
    ("tell me about Control", "Control"),
    ("easy wins in Prey", "Prey"),
    ("roadmap for Hades", "Hades"),
    ("easy wins in Left 4 Dead 2", "Left 4 Dead 2"),
    ("how many achievements in Among Us?", "Among Us"),
    ("closest to 100% in ARK: Survival Evolved", "ARK: Survival Evolved"),
    ("am I close to finishing Portal 2?", "Portal 2"),
    # a sequel we don't own must NOT resolve to the one we do
    ("roadmap for Hades II", None),
    ("roadmap for Portal 3", None),
]

_res = []
def check(label, ok, detail=""):
    _res.append(bool(ok))
    print(f"{'PASS' if ok else 'FAIL'}  {label}" + (f"\n      {detail}" if detail and not ok else ""))

for q, want in CASES:
    hit = _match_owned_game(q, GAMES)
    got = hit[0] if hit else None
    check(f"{q[:44]:44} -> {got!r}", got == want, f"want {want!r}")

# the fast path inherits the same resolver — a library-wide question must stay
# library-wide, and a scoped one must keep its appid.
ctx = {"games": GAMES}
appid, _, ok = _scope("am I inside the top 10 percent?", ctx)
check("fastpath: 'inside' phrasing stays library-wide", appid is None and ok, f"{appid} {ok}")
appid, _, ok = _scope("rarest achievement in Prey", ctx)
check("fastpath: a real game reference still scopes", appid == 4 and ok, f"{appid} {ok}")

print()
p, t = sum(_res), len(_res)
print(f"  {p} passed, {t - p} failed")
sys.exit(0 if p == t else 1)
