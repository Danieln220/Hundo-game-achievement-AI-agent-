"""17.14 — the app's OWN how-to question skips the Pro planner.
Exactly `How do I unlock "<ach>" in <game>?` (sent by the app's guide buttons)
routes straight to how-to with ZERO planner calls; anything else — including
near-misses — keeps the normal planner path. A miss is safe (the planner still
routes it); a false positive would answer a compound question with only a guide.
Run: PYTHONPATH=. python eval/howto_fast_check.py"""
import hashlib, os, shutil, sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
# In-memory/disk only: set EMPTY (load_dotenv never overrides an existing var,
# but re-adds a missing one — a pop would silently hit production Upstash).
os.environ["UPSTASH_REDIS_REST_URL"] = ""

from agent import graph, search
from agent.graph import match_howto_template, howto_identity, plan_code_node

# Every stub below is restored (and the temp cache dir removed) in the finally at
# the bottom, so nothing leaks into another check run in the same process.
_ORIG = {"graph.call_llm": graph.call_llm, "graph.web_search": graph.web_search,
         "search.web_search": search.web_search, "search._CACHE_DIR": search._CACHE_DIR}
_TMP = Path(tempfile.mkdtemp(prefix="howto_cache_"))

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

try:
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

  # ── part 3: which how-to searches are SHARED through the search cache ───────
  # Only a question that names its achievement + game (the app's buttons) is
  # shared; a typed or follow-up question searches fresh every time. Tavily AND
  # the answer model are stubbed here (no network, no spend), the cache is a temp
  # dir with Redis off.
  llm_calls = []
  def _fake_answer(prompt, **kw):
      llm_calls.append(kw.get("model"))
      return "Do the thing. Sources: …"
  graph.call_llm = _fake_answer
  search._CACHE_DIR = _TMP
  tavily = []
  outage = [False]
  def _fake_search(query, max_results=3):
      tavily.append(query)
      return [] if outage[0] else [{"title": f"r{i}", "url": f"https://example.com/{i}", "content": "c"}
                                   for i in range(max_results)]
  graph.web_search = search.web_search = _fake_search
  cached_files = lambda: sorted(f.name for f in _TMP.glob("*.json"))
  key_file = lambda key: hashlib.sha1(key.encode("utf-8")).hexdigest() + ".json"

  def guide(q, history=None):
      return graph.howto_search_node({"question": q, "history": history or []})

  GG = 'How do I unlock "Getting Greedy" in Dragon Ball Z: Kakarot?'
  GG_CAPS = 'How do I unlock "Getting Greedy" in DRAGON BALL Z: KAKAROT?'
  check("identity: casing variants of the template share one identity",
        howto_identity(GG) == howto_identity(GG_CAPS) == "dragon ball z: kakarot|getting greedy",
        howto_identity(GG_CAPS))
  check("identity: typed / follow-up questions have none",
        howto_identity("how do I get Getting Greedy in Kakarot?") is None and howto_identity("how do I unlock it?") is None)

  g1 = guide(GG)
  check("button ask → 1 search, 3 sources", len(tavily) == 1 and len(g1["sources"]) == 3, (tavily, g1.get("sources")))
  check("…stored under the versioned game|achievement key",
        cached_files() == [key_file("howto_guide:v1:dragon ball z: kakarot|getting greedy")], cached_files())
  g2 = guide(GG_CAPS)
  check("same guide, Steam's ALL-CAPS title → cached, no new search",
        len(tavily) == 1 and g2["sources"] == g1["sources"], tavily)

  files_before = cached_files()
  FOLLOW = [{"question": "what's my rarest achievement?", "answer": "Getting Greedy in Kakarot"}]
  guide("how do I unlock it?", FOLLOW)
  guide("how do I unlock it?", [{"question": "rarest?", "answer": "Finnish Ace in War Thunder"}])
  check("follow-up 'how do I unlock it?' → fresh search each time, never shared",
        len(tavily) == 3, tavily)
  guide("how do I get Getting Greedy in Kakarot?")
  guide("how do I get Getting Greedy in Kakarot?")
  check("typed how-to → fresh search each time (as before 17.14)", len(tavily) == 5, tavily)
  check("…and neither wrote anything to the shared cache", cached_files() == files_before, cached_files())
  check("the query is the asker's own wording + the guide suffix",
        tavily[3] == "how do I get Getting Greedy in Kakarot? Steam achievement guide", tavily[3])

  # A roadmap link entry for the SAME achievement (one result, its own key) must
  # not be what the chat's guide search gets back.
  search.cache_put("howto:dragon ball z: kakarot:only the finest",
                   [{"title": "roadmap link", "url": "https://example.com/rm", "content": ""}])
  g3 = guide('How do I unlock "Only the Finest" in Dragon Ball Z: Kakarot?')
  check("a roadmap entry for the same achievement doesn't collide with the chat search",
        len(tavily) == 6 and len(g3["sources"]) == 3 and g3["sources"][0]["title"] == "r0", (tavily[-1:], g3["sources"][:1]))

  outage[0] = True
  q_out = 'How do I unlock "Wish Granted" in Dragon Ball Z: Kakarot?'
  g_out = guide(q_out)
  check("search outage → honest 'couldn't find', no sources", g_out["sources"] == [] and "couldn't find" in g_out["answer"])
  outage[0] = False
  g_back = guide(q_out)
  check("outage was NOT cached → next ask searches again and succeeds",
        len(tavily) == 8 and len(g_back["sources"]) == 3, tavily)
  # 9 guides asked; the outage one answers without the model → 8 model calls
  check("the answer model was the stub every time (no real LLM call)",
        len(llm_calls) == 8, llm_calls)
finally:
  graph.call_llm = _ORIG["graph.call_llm"]
  graph.web_search = _ORIG["graph.web_search"]
  search.web_search = _ORIG["search.web_search"]
  search._CACHE_DIR = _ORIG["search._CACHE_DIR"]
  shutil.rmtree(_TMP, ignore_errors=True)

print("ALL PASSED" if all(_res) else f"{_res.count(False)} FAILED")
sys.exit(0 if all(_res) else 1)
