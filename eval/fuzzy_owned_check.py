"""Regression checks for agent.graph._fuzzy_owned — a sequel is a different game,
not a typo. Run: python eval/fuzzy_owned_check.py"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import pandas as pd
from agent.graph import _fuzzy_owned

games = pd.DataFrame({"appid": [1, 2, 3, 4, 5, 6], "name": [
    "Forza Horizon 4", "Forza Horizon 5", "Left 4 Dead 2", "DARK SOULS III",
    "DOOM (1993)", "Hollow Knight"]})

CASES = [  # (extracted title, expected owned name or None)
    ("Forza Horizon 6", None),
    ("forza horizon 5", "Forza Horizon 5"),
    ("Forza Horizon 4", "Forza Horizon 4"),
    ("Left 4 Ded 2", "Left 4 Dead 2"),
    ("Left 4 Dead", None),
    ("Dark Souls III", "DARK SOULS III"),
    ("Dark Souls II", None),
    ("Dark Souls 3", "DARK SOULS III"),
    ("doom", "DOOM (1993)"),
    ("Hollow Knight", "Hollow Knight"),
    ("Rocket League", None),
]

fails = 0
for title, want in CASES:
    hit = _fuzzy_owned(title, games)
    got = hit[0] if hit else None
    ok = got == want
    fails += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {title!r:22} -> {got!r} (want {want!r})")
print(f"\n{len(CASES) - fails}/{len(CASES)} passed")
sys.exit(1 if fails else 0)
