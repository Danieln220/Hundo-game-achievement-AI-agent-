"""Offline checks for the 23.2 API guards (no network/LLM): rate-limit coverage,
steam_id validation, question/history caps, /chart payload bound.
Run: PYTHONPATH=. python eval/api_guard_check.py"""
import os, sys, time
os.environ.pop("UPSTASH_REDIS_REST_URL", None)   # in-memory limiter, fresh per run
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
m.DEMO_STEAM_ID = _saved

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

print(f"\n{'ALL PASSED' if not fails else f'{fails} FAILED'}")
sys.exit(1 if fails else 0)
