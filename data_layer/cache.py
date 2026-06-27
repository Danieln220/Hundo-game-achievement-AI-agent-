"""Lightweight Redis for rate-limit counters + locks: Upstash REST (prod) or an
in-memory fallback (single-process dev). Backend chosen by config at import time.
requests-only — no new dependency.

Fail-open by design: if Upstash is unreachable, counters return 0 (under limit)
and locks grant — we'd rather risk a duplicate build / an unthrottled request
than lock every user out because the cache hiccuped. Only api/ + data_layer/ use
this; the agent never does.
"""
import threading
import time

import requests

from config import UPSTASH_REDIS_REST_URL, UPSTASH_REDIS_REST_TOKEN, USE_REDIS

_TIMEOUT = 10  # seconds per Upstash REST call


def using_redis() -> bool:
    return USE_REDIS


# ── Upstash REST transport ──────────────────────────────────────────────────────

def _headers() -> dict:
    return {"Authorization": f"Bearer {UPSTASH_REDIS_REST_TOKEN}"}


def _pipeline(commands: list[list]) -> list:
    """Run one or more Redis commands; return the list of results (in order)."""
    r = requests.post(
        f"{UPSTASH_REDIS_REST_URL}/pipeline",
        headers=_headers(),
        json=commands,
        timeout=_TIMEOUT,
    )
    r.raise_for_status()
    return [item.get("result") for item in r.json()]


def _command(*args) -> object:
    return _pipeline([[str(a) for a in args]])[0]


# ── in-memory fallback ───────────────────────────────────────────────────────────
# key -> [value, expiry_epoch | None]
_mem: dict[str, list] = {}
_mem_lock = threading.Lock()


def _mem_alive(entry: list | None) -> bool:
    return bool(entry) and (entry[1] is None or time.time() <= entry[1])


# ── operations ──────────────────────────────────────────────────────────────────

def incr(key: str, ttl_seconds: int) -> int:
    """Increment a counter, setting its TTL on the first hit (fixed window).
    Returns the new count, or 0 if Redis errored (fail-open)."""
    if using_redis():
        try:
            # EXPIRE ... NX only sets the TTL when the key has none, so the window
            # starts on the first request and doesn't slide on every hit.
            res = _pipeline([["INCR", key], ["EXPIRE", key, str(ttl_seconds), "NX"]])
            return int(res[0])
        except Exception:
            return 0
    with _mem_lock:
        entry = _mem.get(key)
        if _mem_alive(entry):
            entry[0] += 1
            return entry[0]
        _mem[key] = [1, time.time() + ttl_seconds]
        return 1


def ttl(key: str) -> int:
    """Seconds until `key` expires (for Retry-After). -1 if unknown/no expiry."""
    if using_redis():
        try:
            v = _command("TTL", key)
            return int(v) if v is not None else -1
        except Exception:
            return -1
    with _mem_lock:
        entry = _mem.get(key)
        if not _mem_alive(entry) or entry[1] is None:
            return -1
        return max(0, int(entry[1] - time.time()))


def exists(key: str) -> bool:
    if using_redis():
        try:
            return bool(_command("EXISTS", key))
        except Exception:
            return False
    with _mem_lock:
        return _mem_alive(_mem.get(key))


def delete(key: str) -> None:
    if using_redis():
        try:
            _command("DEL", key)
        except Exception:
            pass
        return
    with _mem_lock:
        _mem.pop(key, None)


def set(key: str, value: str, ttl_seconds: int | None = None) -> None:
    """Store a string value (optionally with a TTL). Used for async-build status
    + progress (Step 15.4). Best-effort — never raises into a request."""
    if using_redis():
        try:
            if ttl_seconds:
                _command("SET", key, value, "EX", ttl_seconds)
            else:
                _command("SET", key, value)
        except Exception:
            pass
        return
    with _mem_lock:
        _mem[key] = [value, (time.time() + ttl_seconds) if ttl_seconds else None]


def get(key: str) -> str | None:
    if using_redis():
        try:
            return _command("GET", key)
        except Exception:
            return None
    with _mem_lock:
        entry = _mem.get(key)
        return entry[0] if _mem_alive(entry) else None


def acquire_lock(key: str, ttl_seconds: int) -> bool:
    """Best-effort distributed lock via SET NX EX. Returns True if acquired.
    Fail-open: grants the lock if Redis errored (better a rare double-build than a
    deadlocked request)."""
    if using_redis():
        try:
            return _command("SET", key, "1", "NX", "EX", ttl_seconds) == "OK"
        except Exception:
            return True
    with _mem_lock:
        if _mem_alive(_mem.get(key)):
            return False
        _mem[key] = ["1", time.time() + ttl_seconds]
        return True


def release_lock(key: str) -> None:
    delete(key)
