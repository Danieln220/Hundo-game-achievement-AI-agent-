"""Cache-layer checks: the rate-limit window, and the FAIL-CLOSED fallback when
Redis is unreachable (2026-09-15: the Upstash DB vanished and `incr` returned 0
for every request → no rate limit at all). Offline; simulates Redis errors by
monkeypatching the transport.  Run:  py eval/cache_check.py
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data_layer import cache  # noqa: E402

_results: list[bool] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    _results.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {label}" + (f"\n      {detail}" if detail and not ok else ""))


def _boom(*_a, **_k):
    raise ConnectionError("simulated: Redis host does not resolve")


# ── in-memory window semantics (what dev runs, and what prod now falls back to) ─
real_using, real_pipeline = cache.using_redis, cache._pipeline
try:
    cache.using_redis = lambda: False
    k = f"chk:mem:{time.time()}"
    counts = [cache.incr(k, 60) for _ in range(3)]
    check("in-memory: fixed window counts 1,2,3", counts == [1, 2, 3], str(counts))
    check("in-memory: ttl is within the window", 0 < cache.ttl(k) <= 60, str(cache.ttl(k)))
    k2 = f"chk:exp:{time.time()}"
    cache.incr(k2, 1)
    time.sleep(1.2)
    check("in-memory: window expires and restarts at 1", cache.incr(k2, 1) == 1)

    # ── Redis configured but UNREACHABLE → must fall back, never return 0 ────
    cache.using_redis = lambda: True
    cache._pipeline = _boom
    k3 = f"chk:dead:{time.time()}"
    counts = [cache.incr(k3, 60) for _ in range(3)]
    check("dead redis: incr falls back to in-memory (1,2,3), not 0", counts == [1, 2, 3], str(counts))
    check("dead redis: ttl falls back to in-memory window", 0 < cache.ttl(k3) <= 60, str(cache.ttl(k3)))
    check("dead redis: ping() is False", cache.ping() is False)
    check("dead redis: acquire_lock stays FAIL-OPEN (grants)", bool(cache.acquire_lock(f"chk:lock:{time.time()}", 30)))
    check("dead redis: set/get degrade silently", (cache.set("chk:x", "1", 5) is None) and cache.get("chk:x") is None)
finally:
    cache.using_redis, cache._pipeline = real_using, real_pipeline

# ── 23.3c: OWNED locks (in-memory backend — same semantics as the Redis path) ──
import importlib, os
os.environ["UPSTASH_REDIS_REST_URL"] = ""
os.environ["UPSTASH_REDIS_REST_TOKEN"] = ""
import config as _cfg
importlib.reload(_cfg)
_mem_cache = importlib.reload(cache)
check("in-memory backend for lock checks", not _mem_cache.using_redis())

K = "chk:ownedlock"
t1 = _mem_cache.acquire_lock(K, 30)
check("acquire returns a token", bool(t1))
check("second acquire blocked while held", _mem_cache.acquire_lock(K, 30) is None)
check("heartbeat works for the owner", _mem_cache.refresh_lock(K, 30, t1) is True)
check("heartbeat REJECTED for a stale token", _mem_cache.refresh_lock(K, 30, "stale-token") is False)
_mem_cache.release_lock(K, "stale-token")
check("stale token can NOT release the owner's lock", _mem_cache.exists(K))
_mem_cache.release_lock(K, t1)
check("owner releases its own lock", not _mem_cache.exists(K))

# takeover after expiry: the old owner must not be able to extend or delete
t_old = _mem_cache.acquire_lock(K, 1)
time.sleep(1.1)
t_new = _mem_cache.acquire_lock(K, 30)
check("expired lock can be taken over", bool(t_new) and t_new != t_old)
check("stalled build's heartbeat fails after takeover", _mem_cache.refresh_lock(K, 30, t_old) is False)
_mem_cache.release_lock(K, t_old)
check("stalled build can NOT delete the new owner's lock", _mem_cache.exists(K))
_mem_cache.release_lock(K, t_new)

print()
passed, total = sum(_results), len(_results)
print(f"  {passed} passed, {total - passed} failed")
sys.exit(0 if passed == total else 1)

