"""17.14 — the app's OWN how-to question skips the Pro planner.
Exactly `How do I unlock "<ach>" in <game>?` (sent by the app's guide buttons)
routes straight to how-to with ZERO planner calls; anything else — including
near-misses — keeps the normal planner path. A miss is safe (the planner still
routes it); a false positive would answer a compound question with only a guide.
Run: PYTHONPATH=. python eval/howto_fast_check.py"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent import graph
from agent.graph import match_howto_template, plan_code_node

# (question, expected (achievement, game) or None)
CASES = [
    # the button template, with the kinds of names real libraries contain
    ('How do I unlock "Getting Greedy" in Dragon Ball Z: Kakarot?',
     ("Getting Greedy", "Dragon Ball Z: Kakarot")),
    ('How do I unlock "Only the Finest" in DRAGON BALL Z: KAKAROT?',
     ("Only the Finest", "DRAGON BALL Z: KAKAROT")),
    ('How do I unlock "Hide "n" Seek" in Left 4 Dead 2?', ('Hide "n" Seek', "Left 4 Dead 2")),
    ('How do I unlock "100%" in Portal 2?', ("100%", "Portal 2")),
    ('How do I unlock "Who Am I?" in Hades?', ("Who Am I?", "Hades")),
    # a title that itself ends in '?' — the template adds a second one
    ('How do I unlock "Level 10" in Is It Wrong to Pick Up Girls in a Dungeon??',
     ("Level 10", "Is It Wrong to Pick Up Girls in a Dungeon?")),
    ('  How do I unlock "Getting Greedy" in Dragon Ball Z: Kakarot?  ',
     ("Getting Greedy", "Dragon Ball Z: Kakarot")),
    # near-misses — must take the planner
    ('How do I unlock "Getting Greedy" in Dragon Ball Z: Kakarot? Also what is my rarest?', None),
    ('How do I unlock "Getting Greedy" in Kakarot?\nAnd build me a roadmap', None),
    ("How do I unlock Getting Greedy in Dragon Ball Z: Kakarot?", None),   # typed, no quotes
    ('how do i unlock "Getting Greedy" in Dragon Ball Z: Kakarot?', None),  # not the app's casing
    ('How do I unlock "Getting Greedy" in Dragon Ball Z: Kakarot', None),   # no '?'
    ('How do I unlock "" in Dragon Ball Z: Kakarot?', None),
    ('How do I unlock "Getting Greedy" in ?', None),
    ("How do I unlock the rarest achievement in my library?", None),        # a suggestion chip
    ("How do I unlock ", None),                                              # the prefill chip
    ('Why can\'t I unlock "Getting Greedy" in Dragon Ball Z: Kakarot?', None),
    ('How long to unlock "Getting Greedy" in Dragon Ball Z: Kakarot?', None),
    ("", None),
]

_res = []
def check(label, ok, detail=""):
    _res.append(bool(ok))
    print(f"{'PASS' if ok else 'FAIL'}  {label}" + (f"\n      {detail}" if detail and not ok else ""))

for q, want in CASES:
    got = match_howto_template(q)
    check(f"{q.strip()[:60]!r:64} -> {got}", got == want, f"want {want}")

# plan_code_node must not reach the model for the template — and MUST for a near-miss.
calls = []
def _fake_llm(prompt, **kw):
    calls.append(kw.get("model"))
    return "ROUTE: howto\nINTERPRETATION: NONE\nCLARIFY: NONE\nPLAN:\n- x\nCODE:\n"

graph.call_llm = _fake_llm
out = plan_code_node({"question": CASES[0][0], "schema": "", "history": []})
check("template -> route howto", out.get("route") == "howto", str(out))
check("template -> 0 planner calls", calls == [], str(calls))
check("template -> no code written", "last_code" not in out, str(out))
check("template -> plan names the achievement", "Getting Greedy" in (out.get("plan") or ""), out.get("plan"))

calls.clear()
plan_code_node({"question": CASES[7][0], "schema": "", "history": []})
check("near-miss -> planner still called once", len(calls) == 1, str(calls))

# the graph wires route=howto to the how-to node
check("route howto -> howto_search node",
      graph.route_after_plan_code({"route": "howto"}) == "howto_search")

print("ALL PASSED" if all(_res) else f"{_res.count(False)} FAILED")
sys.exit(0 if all(_res) else 1)
