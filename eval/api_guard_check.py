"""Offline checks for the 23.2 API guards (no network/LLM): rate-limit coverage,
steam_id validation, question/history caps, /chart payload bound.
Run: PYTHONPATH=. python eval/api_guard_check.py"""
import hashlib, os, sys, time
# In-memory cache, fresh per run. Set EMPTY rather than popped: config.py calls
# load_dotenv(), which re-adds a MISSING var from .env (so a pop silently ran this
# suite against production Upstash) but never overrides one that exists.
os.environ["UPSTASH_REDIS_REST_URL"] = ""
os.environ.update(RATE_LIMIT_READ_PER_MIN="3", RATE_LIMIT_CHART_PER_MIN="2",
                  RATE_LIMIT_STATUS_PER_MIN="3")
from fastapi.testclient import TestClient
import api.main as m

# Rate-limit counters are keyed by client IP and (with Redis configured) SURVIVE
# between runs — so each REQUEST gets its own fresh IP, except where a test is
# deliberately hammering one (those hold an IP via ip() once). Without this the
# second run inside the same minute starts already over the limit.
_RUN, _N = os.urandom(2).hex(), [0]
def ip() -> str:
    _N[0] += 1
    return f"10.{int(_RUN[:2], 16)}.{_N[0] // 250 + int(_RUN[2:], 16) % 5}.{_N[0] % 250 + 1}"

c = TestClient(m.app)
fails = 0
def check(name, cond, extra=""):
    global fails
    fails += not cond
    print(f"{'PASS' if cond else 'FAIL'}  {name} {extra}")

BAD = ["../../etc", "7656119894619624", "76561198946196245x", "abc"]
for ep in ["/library", "/popular", "/session/status", "/memory"]:
    for bad in BAD:
        r = c.get(ep, params={"steam_id": bad}, headers={"X-Forwarded-For": ip()})
        check(f"{ep} rejects {bad!r}", r.status_code == 400, r.status_code)
r = c.delete("/memory", params={"steam_id": "../x"}, headers={"X-Forwarded-For": ip()})
check("DELETE /memory rejects bad id", r.status_code == 400, r.status_code)

r = c.post("/ask", json={"question": "hi", "steam_id": "../../x"})
check("/ask bad steam_id → 400 string detail", r.status_code == 400 and isinstance(r.json()["detail"], str), r.status_code)
r = c.post("/ask", json={"question": "x" * 1001, "steam_id": "76561198946196245"})
check("/ask over-long question → 400", r.status_code == 400, r.status_code)
r = c.post("/ask/stream", json={"question": "   ", "steam_id": "76561198946196245"})
check("/ask/stream empty question → 400", r.status_code == 400, r.status_code)

h = [{"question": f"q{i}", "answer": "a" * 5000} for i in range(20)]
req = m.AskReq(question="q", history=h)
check("history trimmed to 6 turns", len(req.history) == 6, len(req.history))
check("history answers clipped", all(len(t["answer"]) <= 602 for t in req.history))
check("history keeps the LAST turns", req.history[-1]["question"] == "q19")

r = c.post("/chart", json={"result": {"steam_id": "../../etc", "last_result": "x"}},
           headers={"X-Forwarded-For": ip()})
check("/chart bad steam_id → 400", r.status_code == 400, r.status_code)
r = c.post("/chart", json={"result": {"last_result": "x" * 70_000}}, headers={"X-Forwarded-For": ip()})
check("/chart oversized → 413", r.status_code == 413, r.status_code)

# rate limits now cover the previously-unmetered endpoints
pop_ip = ip()
codes = [c.get("/popular", params={"steam_id": "bad"}, headers={"X-Forwarded-For": pop_ip}).status_code
         for _ in range(4)]
check("/popular limited after 3/min", codes[-1] == 429, codes)
chart_ip = ip()
codes = [c.post("/chart", json={"result": {}}, headers={"X-Forwarded-For": chart_ip}).status_code
         for _ in range(3)]
check("/chart limited after 2/min", codes[-1] == 429, codes)
status_ip = ip()
codes = [c.get("/session/status", params={"steam_id": "bad"}, headers={"X-Forwarded-For": status_ip}).status_code
         for _ in range(4)]
check("/session/status limited after 3/min", codes[-1] == 429, codes)
r = c.get("/popular", params={"steam_id": "bad"}, headers={"X-Forwarded-For": pop_ip,
                                                              "Origin": m.CORS_ORIGINS[0]})
check("429 carries CORS + Retry-After", r.status_code == 429 and "retry-after" in r.headers
      and "access-control-allow-origin" in r.headers, r.status_code)

# happy paths still work (valid id; fast-path question → no LLM spend)
SID = "76561198946196245"
if m.has_snapshot(SID):
    r = c.get("/library", params={"steam_id": SID}, headers={"X-Forwarded-For": ip()})
    check("/library valid id → 200", r.status_code == 200, r.status_code)
    r = c.post("/ask", json={"question": "what is my rarest achievement?", "steam_id": SID,
                             "history": h}, headers={"X-Forwarded-For": ip()})
    check("/ask valid (fast-path) → 200 + answer", r.status_code == 200 and r.json().get("answer"),
          (r.status_code, r.json().get("route")))
else:
    print("SKIP  happy paths (no local snapshot for", SID, ")")

# ── 23.6g: the answer-cache key must survive a cosmetic memory rewrite ───────
fp = m._memory_fingerprint
MEM = "- Only plays single-player games\n- Goal: 100% Hollow Knight"
check("memory fingerprint ignores bullet ORDER",
      fp(MEM) == fp("- Goal: 100% Hollow Knight\n- Only plays single-player games"))
check("memory fingerprint ignores case/whitespace",
      fp(MEM) == fp("-   only plays SINGLE-PLAYER games\n- goal: 100% hollow knight"))
check("memory fingerprint changes on a real new fact",
      fp(MEM) != fp(MEM + "\n- Prefers short games"))
check("empty memory is stable", fp("") == fp(None))
k1 = m._answer_cache_key(m.AskReq(question="rarest?", steam_id=SID), MEM)
k2 = m._answer_cache_key(m.AskReq(question="  Rarest? ", steam_id=SID), MEM + "  ")
check("same question + same memory shape → same cache key", k1 is not None and k1 == k2, f"{k1} vs {k2}")

# ── 23.6g: the answer-cache key must survive a cosmetic memory rewrite ───────
fp = m._memory_fingerprint
MEM = "- Only plays single-player games\n- Goal: 100% Hollow Knight"
check("memory fingerprint ignores bullet ORDER",
      fp(MEM) == fp("- Goal: 100% Hollow Knight\n- Only plays single-player games"))
check("memory fingerprint ignores case/whitespace",
      fp(MEM) == fp("-   only plays SINGLE-PLAYER games\n- goal: 100% hollow knight"))
check("memory fingerprint changes on a real new fact",
      fp(MEM) != fp(MEM + "\n- Prefers short games"))
check("empty memory is stable", fp("") == fp(None))
k1 = m._answer_cache_key(m.AskReq(question="rarest?", steam_id=SID), MEM)
k2 = m._answer_cache_key(m.AskReq(question="  Rarest? ", steam_id=SID), MEM + "  ")
check("same question + same memory shape → same cache key", k1 is not None and k1 == k2, f"{k1} vs {k2}")

# ── 23.4b: memory is gated on a VERIFIED (Steam OpenID) identity ─────────────
OTHER = "76561197960265728"
tok = m.issue_auth_token(SID)
check("a token is issued (AUTH_SECRET configured)", bool(tok))
H = {"X-Hundo-Auth": tok, "X-Forwarded-For": ip()}

r = c.get("/memory", params={"steam_id": SID}, headers={"X-Forwarded-For": ip()})
check("GET /memory without a token → empty + verified:false",
      r.status_code == 200 and r.json() == {"memory": "", "verified": False}, r.json())
r = c.get("/memory", params={"steam_id": SID}, headers=H)
check("GET /memory WITH the token → verified:true",
      r.status_code == 200 and r.json().get("verified") is True, r.status_code)
r = c.get("/memory", params={"steam_id": OTHER}, headers={**H, "X-Forwarded-For": ip()})
check("a token for one id can't read ANOTHER id's memory", r.json().get("verified") is False, r.json())
r = c.request("DELETE", "/memory", params={"steam_id": SID}, headers={"X-Forwarded-For": ip()})
check("DELETE /memory without a token → 403", r.status_code == 403, r.status_code)

def _req(token: str):
    return m.Request({"type": "http", "headers": [(b"x-hundo-auth", token.encode())]})

for bad, label in [
    (f"{SID}.99999999999.deadbeefdeadbeefdeadbeefdeadbeef", "forged signature"),
    (f"{OTHER}." + tok.split(".", 1)[1], "swapped steam_id, kept signature"),
    (tok.replace(".", "-"), "malformed token"),
    ("", "empty token"),
]:
    check(f"rejected: {label}", m.verified_steam_id(_req(bad)) is None)

import hmac as _h, hashlib as _hl
from config import AUTH_SECRET as _S
_body = f"{SID}.{int(time.time()) - 10}"
_expired = f"{_body}.{_h.new(_S.encode(), _body.encode(), _hl.sha256).hexdigest()[:32]}"
check("rejected: expired but correctly signed token", m.verified_steam_id(_req(_expired)) is None)
check("accepted: a valid token round-trips", m.verified_steam_id(_req(tok)) == SID)

# ── 17.12: /demo/plan — rate-limited, 404 without a demo, and the plan itself
check("/demo/plan is rate-limited (read bucket)", "/demo/plan" in m._RATE_RULES)
_saved = m.DEMO_STEAM_ID
m.DEMO_STEAM_ID = ""
r = c.get("/demo/plan", headers={"X-Forwarded-For": ip()})
check("/demo/plan → 404 when no demo is configured", r.status_code == 404, r.status_code)
# The demo alias must never reach vanity resolution ("demo" is a real Steam
# custom URL → a stranger's profile under the demo button).
_resolved, _real_resolve = [], m.resolve_steam_id
m.resolve_steam_id = lambda p: _resolved.append(p) or "76561190000000003"
r = c.post("/session", json={"profile": "demo"}, headers={"X-Forwarded-For": ip()})
check("/session 'demo' → 404 when no demo is configured", r.status_code == 404, r.status_code)
check("/session 'demo' → readable demo message", "demo isn't available" in str(r.json().get("detail")))
check("/session 'demo' never calls the Steam resolver", _resolved == [], _resolved)
m.resolve_steam_id = _real_resolve
m.DEMO_STEAM_ID = _saved

# ── code review 2026-09-24, fix 7: a build waiting for a pool worker is QUEUED,
# not stuck — the poller gets queued:true (and pauses its stall timer); a queue
# older than SNAPSHOT_WAIT_MAX died with its process → retry hint; the worker
# flips it to "building" when it starts. Everything external is stubbed here.
QID, QID2 = "76561190000000001", "76561190000000002"  # below the lowest real SteamID64 — nobody's account
_saved_fns = dict(has_snapshot=m.has_snapshot, ensure_snapshot=m.ensure_snapshot,
                  resolve_steam_id=m.resolve_steam_id, is_private=m.is_private_owned_payload,
                  record=m._record_snapshot_meta, sweep_c=m._sweep_charts, sweep_s=m._sweep_snapshots,
                  pool=m._BUILD_POOL, get_snap=m.db.get_snapshot, up_snap=m.db.upsert_snapshot,
                  up_user=m.db.upsert_user, owned=m.steam_client.get_owned_games)
_db_writes = []
m.has_snapshot = lambda s: False
m.db.get_snapshot = lambda s: None
m.db.upsert_snapshot = lambda s, **kw: _db_writes.append(kw.get("status"))
m.db.upsert_user = lambda *a, **kw: None
status = lambda sid: c.get("/session/status", params={"steam_id": sid},
                           headers={"X-Forwarded-For": ip()}).json()

m.cache.set(m._status_key(QID), m._queued_status(), 60)
r = status(QID)
check("status: fresh queue → building + queued:true", r.get("status") == "building" and r.get("queued") is True, r)
m.cache.set(m._status_key(QID), f"queued:{int(time.time()) - m.SNAPSHOT_WAIT_MAX - 5}", 60)
r = status(QID)
check("status: queue older than SNAPSHOT_WAIT_MAX → failed + retry hint",
      r.get("status") == "failed" and "load the profile again" in r.get("error", ""), r)
m.cache.set(m._status_key(QID), "building", 60)
m.cache.set(m._progress_key(QID), "3/10", 60)
r = status(QID)
check("status: running build → progress, no queued flag",
      r.get("status") == "building" and "queued" not in r and r["progress"]["done"] == 3, r)
# Redis down / key gone → the DB row is the fallback, and it knows "queued" too
m.db.get_snapshot = lambda s: {"status": "queued", "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
r = status(QID2)
check("status: no cache entry + DB row 'queued' → queued:true", r.get("status") == "building" and r.get("queued") is True, r)
m.db.get_snapshot = lambda s: None

# /session marks the build queued BEFORE it reaches the pool (DB mirror too)
_submitted = []
class _Pool:
    def submit(self, fn, sid, *a):
        _submitted.append(m.cache.get(m._status_key(sid)))
m._BUILD_POOL = _Pool()
m.resolve_steam_id = lambda p: QID2
m.is_private_owned_payload = lambda o: False
m.steam_client.get_owned_games = lambda s: {"response": {"game_count": 1}}
m._sweep_charts = m._sweep_snapshots = lambda: 0
_db_writes.clear()
r = c.post("/session", json={"profile": "somebody"}, headers={"X-Forwarded-For": ip()}).json()
check("/session: new build is queued when submitted",
      r.get("status") == "building" and len(_submitted) == 1 and str(_submitted[0]).startswith("queued:"), (r, _submitted))
check("/session: DB row mirrors 'queued'", _db_writes == ["queued"], _db_writes)
# a refresh while one is queued doesn't stack a second build
_submitted.clear()
m.cache.set(m._status_key(QID), m._queued_status(), 60)
check("refresh while queued → not started again", m._refresh_in_background(QID) is False and _submitted == [], _submitted)

# the worker flips queued → building as it starts; only a FIRST build writes the DB row
_seen = {}
m.ensure_snapshot = lambda sid, **kw: _seen.setdefault(sid, m.cache.get(m._status_key(sid)))
m._record_snapshot_meta = lambda sid: None
_db_writes.clear()
m.cache.set(m._status_key(QID), m._queued_status(), 60)
m._run_build(QID, {})
check("worker: queued → building when it starts", _seen.get(QID) == "building", _seen)
check("worker: first build mirrors 'building' to the DB", _db_writes == ["building"], _db_writes)
_db_writes.clear()
m._run_build(QID2, None, True)
check("worker: a refresh leaves the DB row alone at start", "building" not in _db_writes, _db_writes)

(m.has_snapshot, m.ensure_snapshot, m.resolve_steam_id, m.is_private_owned_payload, m._record_snapshot_meta,
 m._sweep_charts, m._sweep_snapshots, m._BUILD_POOL, m.db.get_snapshot, m.db.upsert_snapshot,
 m.db.upsert_user, m.steam_client.get_owned_games) = _saved_fns.values()

from data_layer.library import next_plan
_lib = {
    "curator": {"closest": {"game": "B", "pct": 75.0, "remaining": 1}},
    "games": [
        {"game": "A", "total": 4, "unlocked": 4, "pct": 100.0, "achievements": []},
        {"game": "B", "total": 4, "unlocked": 3, "pct": 75.0, "achievements": [
            {"name": "done", "desc": "x", "pct": 50.0, "hidden": False, "achieved": True},
            {"name": "hard", "desc": "y", "pct": 2.0, "hidden": False, "achieved": False},
            {"name": "easy", "desc": "z", "pct": 40.0, "hidden": False, "achieved": False},
            {"name": "mystery", "desc": "", "pct": None, "hidden": True, "achieved": False},
            {"name": "All done", "desc": "Obtain all achievements.", "pct": 1.0, "hidden": False, "achieved": False},
        ]},
    ],
}
_plan = next_plan(_lib)
check("next_plan picks the curator's closest game", _plan and _plan["game"] == "B" and _plan["left"] == 1)
check("next_plan orders locked easiest-first, unknown rarity last", [a["name"] for a in _plan["locked"]] == ["easy", "hard", "mystery"])
check("next_plan splits the meta-achievement out", _plan["meta"] and _plan["meta"]["name"] == "All done")
check("next_plan keeps the hidden flag", _plan["locked"][2]["hidden"] is True)
check("next_plan → None when every game is complete", next_plan({"curator": {}, "games": [_lib["games"][0]]}) is None)

# ── 17.14: the app's how-to template shares ONE cached guide across users ─────
# Everything external is stubbed; the cache is the in-memory one (see top).
GQ = 'How do I unlock "Getting Greedy" in Dragon Ball Z: Kakarot?'
GQ_CAPS = 'How do I unlock "Getting Greedy" in DRAGON BALL Z: KAKAROT?'
A, B = "76561190000000011", "76561190000000012"   # below the lowest real SteamID64
key = lambda q, sid, mem="", hist=None: m._answer_cache_key(
    m.AskReq(question=q, steam_id=sid, history=hist), mem, sid)
kA = key(GQ, A)
check("template → shared guide key", bool(kA) and kA.startswith(m._SHARED_PREFIX), kA)
# pinned: guides already cached in production (since 9e0f07e) must keep hitting
check("shared key format unchanged: prefix + sha1('game|achievement')",
      kA == m._SHARED_PREFIX + hashlib.sha1(b"dragon ball z: kakarot|getting greedy").hexdigest(), kA)
check("shared key: same for another user, other memory, a follow-up, other casing",
      kA == key(GQ, B, "- Goal: 100% Hollow Knight") == key(GQ, B, hist=[{"question": "q", "answer": "a"}])
      == key(GQ_CAPS, B))
check("shared key: another achievement → another key",
      kA != key('How do I unlock "Only the Finest" in Dragon Ball Z: Kakarot?', A))
check("typed how-to (no quotes) keeps the per-user path",
      not (key("How do I unlock Getting Greedy in Dragon Ball Z: Kakarot?", A) or "").startswith(m._SHARED_PREFIX))

SRC = [{"title": "Guide", "url": "https://example.com/g", "content": "summon Shenron"}]
m._answer_cache_put(kA, {"route": "howto", "answer": "Summon Shenron 5 times.", "sources": SRC,
                         "plan": "p", "done": True, "question": GQ, "steam_id": A,
                         "memory": "- A's private goal", "llm_usage": {"llm_calls": 1}})
hit = m._answer_cache_get(kA, GQ_CAPS, B)
check("shared hit serves the guide", hit and hit["answer"] == "Summon Shenron 5 times." and hit["sources"] == SRC, hit)
check("shared hit carries the REQUESTER's echo, not the first asker's",
      hit and hit["steam_id"] == B and hit["question"] == GQ_CAPS and hit.get("cached") is True, hit)
check("shared entry never stores memory / usage", hit and "memory" not in hit and "llm_usage" not in hit, hit)
hit = m._answer_cache_get(kA, GQ, m.DEMO_STEAM_ID or A, demo=True)
check("shared hit for the demo → alias, never a real id", hit and hit["steam_id"] == m.DEMO_ALIAS, hit)

kN = key('How do I unlock "Nope" in Nowhere?', A)
m._answer_cache_put(kN, {"route": "howto", "answer": "I couldn't find a guide", "sources": [], "done": True})
check("a sourceless 'couldn't find a guide' is NOT cached (shared)", m._answer_cache_get(kN) is None)
kP = key("How do I get the nope achievement in Nowhere?", A)
if kP:
    m._answer_cache_put(kP, {"route": "howto", "answer": "I couldn't find a guide", "sources": [], "done": True})
check("…nor on the per-user path", not kP or m._answer_cache_get(kP) is None)
kX = key('How do I unlock "Odd" in Route?', A)
m._answer_cache_put(kX, {"route": "analysis", "answer": "42", "done": True})
check("a shared key only ever stores a how-to", m._answer_cache_get(kX) is None)

# end to end: the second user's click never reaches the agent
_saved_ask = dict(run=m.run, fast=m.fast_answer, log=m.db.log_query)
_runs = []
def _fake_run(q, steam_id=None, history=None, with_insight=False, memory=None):
    _runs.append(steam_id)
    return {"route": "howto", "answer": "Collect the balls.", "sources": SRC, "plan": "p",
            "done": True, "question": q, "steam_id": steam_id, "memory": memory}
m.run, m.fast_answer, m.db.log_query = _fake_run, (lambda q, s: None), (lambda *a, **kw: None)
Q2 = 'How do I unlock "Wish Granted" in Dragon Ball Z: Kakarot?'
r1 = c.post("/ask", json={"question": Q2, "steam_id": A}, headers={"X-Forwarded-For": ip()}).json()
r2 = c.post("/ask", json={"question": Q2, "steam_id": B}, headers={"X-Forwarded-For": ip()}).json()
check("/ask: first asker runs the agent, second is served from the shared guide",
      _runs == [A] and not r1.get("cached") and r2.get("cached") is True and r2.get("steam_id") == B,
      (_runs, r2.get("steam_id")))
body = c.post("/ask/stream", json={"question": Q2, "steam_id": B},
              headers={"X-Forwarded-For": ip()}).text
check("/ask/stream: shared guide is one instant result event, no agent run",
      _runs == [A] and body.count("event: result") == 1 and '"cached": true' in body, (_runs, body[:120]))
m.run, m.fast_answer, m.db.log_query = _saved_ask["run"], _saved_ask["fast"], _saved_ask["log"]

print(f"\n{'ALL PASSED' if not fails else f'{fails} FAILED'}")
sys.exit(1 if fails else 0)
