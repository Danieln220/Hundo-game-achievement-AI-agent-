"""17.14 — the app's OWN how-to question skips the Pro planner.
Exactly `How do I unlock "<ach>" in <game>?` (sent by the app's guide buttons)
routes straight to how-to with ZERO planner calls; anything else — including
near-misses — keeps the normal planner path. A miss is safe (the planner still
routes it); a false positive would answer a compound question with only a guide.
Run: PYTHONPATH=. python eval/howto_fast_check.py"""
import os, sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
# In-memory/disk only: set EMPTY (load_dotenv never overrides an existing var,
# but re-adds a missing one — a pop would silently hit production Upstash).
os.environ["UPSTASH_REDIS_REST_URL"] = ""

from agent import graph, search
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

# ── part 3: the chat how-to search goes through the SHARED search cache ───────
# Tavily is stubbed and the cache points at a temp dir — no network, no prod.
search._CACHE_DIR = Path(tempfile.mkdtemp(prefix="howto_cache_"))
tavily = []
outage = [False]
def _fake_search(query, max_results=3):
    tavily.append(query)
    return [] if outage[0] else [{"title": f"r{i}", "url": f"https://example.com/{i}", "content": "c"}
                                 for i in range(max_results)]
search.web_search = _fake_search

def guide(q):
    return graph.howto_search_node({"question": q})

g1 = guide(CASES[0][0])
check("first ask → 1 search, 3 sources", len(tavily) == 1 and len(g1["sources"]) == 3, (tavily, g1.get("sources")))
g2 = guide(CASES[1][0].replace("Only the Finest", "Getting Greedy"))   # same ach, ALL-CAPS title
check("same guide, Steam's ALL-CAPS title → cached, no new search",
      len(tavily) == 1 and g2["sources"] == g1["sources"], tavily)
guide("how do I get  Getting Greedy in kakarot?")
guide("How do I get getting greedy in Kakarot")
check("typed how-to: repeats (case/space/'?') share one search", len(tavily) == 2, tavily)
check("…and the query sent is the asker's own wording + the guide suffix",
      tavily[1] == "how do I get  Getting Greedy in kakarot? Steam achievement guide", tavily[1])
guide('How do I unlock "Only the Finest" in Dragon Ball Z: Kakarot?')
check("another achievement → its own search", len(tavily) == 3, tavily)

outage[0] = True
q_out = 'How do I unlock "Wish Granted" in Dragon Ball Z: Kakarot?'
g_out = guide(q_out)
check("search outage → honest 'couldn't find', no sources", g_out["sources"] == [] and "couldn't find" in g_out["answer"])
outage[0] = False
g_back = guide(q_out)
check("outage was NOT cached → next ask searches again and succeeds",
      len(tavily) == 5 and len(g_back["sources"]) == 3, tavily)
check("roadmap's per-achievement link cache is a different namespace",
      search.cache_get("howto:dragon ball z: kakarot:getting greedy") is None)

print("ALL PASSED" if all(_res) else f"{_res.count(False)} FAILED")
sys.exit(0 if all(_res) else 1)
