"""Offline checks for the 23.2 API guards (no network/LLM): rate-limit coverage,
steam_id validation, question/history caps, /chart payload bound.
Run: PYTHONPATH=. python eval/api_guard_check.py"""
import os, sys
os.environ.pop("UPSTASH_REDIS_REST_URL", None)   # in-memory limiter, fresh per run
os.environ.update(RATE_LIMIT_READ_PER_MIN="3", RATE_LIMIT_CHART_PER_MIN="2",
                  RATE_LIMIT_STATUS_PER_MIN="3")
from fastapi.testclient import TestClient
import api.main as m

c = TestClient(m.app)
fails = 0
def check(name, cond, extra=""):
    global fails
    fails += not cond
    print(f"{'PASS' if cond else 'FAIL'}  {name} {extra}")

BAD = ["../../etc", "7656119894619624", "76561198946196245x", "abc"]
for ep in ["/library", "/popular", "/session/status", "/memory"]:
    for bad in BAD:
        r = c.get(ep, params={"steam_id": bad}, headers={"X-Forwarded-For": f"10.0.0.{len(bad)}"})
        check(f"{ep} rejects {bad!r}", r.status_code == 400, r.status_code)
r = c.delete("/memory", params={"steam_id": "../x"}, headers={"X-Forwarded-For": "10.1.1.1"})
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
           headers={"X-Forwarded-For": "10.2.2.2"})
check("/chart bad steam_id → 400", r.status_code == 400, r.status_code)
r = c.post("/chart", json={"result": {"last_result": "x" * 70_000}}, headers={"X-Forwarded-For": "10.3.3.3"})
check("/chart oversized → 413", r.status_code == 413, r.status_code)

# rate limits now cover the previously-unmetered endpoints
codes = [c.get("/popular", params={"steam_id": "bad"}, headers={"X-Forwarded-For": "10.9.9.9"}).status_code
         for _ in range(4)]
check("/popular limited after 3/min", codes[-1] == 429, codes)
codes = [c.post("/chart", json={"result": {}}, headers={"X-Forwarded-For": "10.8.8.8"}).status_code
         for _ in range(3)]
check("/chart limited after 2/min", codes[-1] == 429, codes)
codes = [c.get("/session/status", params={"steam_id": "bad"}, headers={"X-Forwarded-For": "10.7.7.7"}).status_code
         for _ in range(4)]
check("/session/status limited after 3/min", codes[-1] == 429, codes)
r = c.get("/popular", params={"steam_id": "bad"}, headers={"X-Forwarded-For": "10.9.9.9",
                                                              "Origin": m.CORS_ORIGINS[0]})
check("429 carries CORS + Retry-After", r.status_code == 429 and "retry-after" in r.headers
      and "access-control-allow-origin" in r.headers, r.status_code)

# happy paths still work (valid id; fast-path question → no LLM spend)
SID = "76561198946196245"
if m.has_snapshot(SID):
    r = c.get("/library", params={"steam_id": SID}, headers={"X-Forwarded-For": "10.4.4.4"})
    check("/library valid id → 200", r.status_code == 200, r.status_code)
    r = c.post("/ask", json={"question": "what is my rarest achievement?", "steam_id": SID,
                             "history": h}, headers={"X-Forwarded-For": "10.5.5.5"})
    check("/ask valid (fast-path) → 200 + answer", r.status_code == 200 and r.json().get("answer"),
          (r.status_code, r.json().get("route")))
else:
    print("SKIP  happy paths (no local snapshot for", SID, ")")

print(f"\n{'ALL PASSED' if not fails else f'{fails} FAILED'}")
sys.exit(1 if fails else 0)
