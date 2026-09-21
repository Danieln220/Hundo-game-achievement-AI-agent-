"""Lightweight Redis for rate-limit counters + locks: Upstash REST (prod) or an
in-memory fallback (single-process dev). Backend chosen by config at import time.
requests-only — no new dependency.

Locks FAIL OPEN: if Upstash is unreachable they grant — better a rare duplicate
build than a deadlocked request. Rate-limit counters FAIL CLOSED to the
per-instance in-memory window instead (23.2b): returning "under limit" on a cache
outage left LLM spend completely unguarded (2026-09-15 incident). Only api/ +
data_layer/ use this; the agent never does.
"""
import threading
import time
import uuid
from typing import Optional

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

def _mem_sweep_locked() -> None:
    """Drop expired in-memory entries (caller holds _mem_lock). Cheap, and only
    run when the map has grown — keeps the fallback limiter from leaking keys."""
    if len(_mem) > 5000:
        now = time.time()
        for k in [k for k, v in _mem.items() if v[1] is not None and v[1] < now]:
            _mem.pop(k, None)


def _mem_incr(key: str, ttl_seconds: int) -> int:
    with _mem_lock:
        entry = _mem.get(key)
        if _mem_alive(entry):
            entry[0] += 1
            return entry[0]
        _mem_sweep_locked()
        _mem[key] = [1, time.time() + ttl_seconds]
        return 1


def _mem_ttl(key: str) -> int:
    with _mem_lock:
        entry = _mem.get(key)
        if not _mem_alive(entry) or entry[1] is None:
            return -1
        return max(0, int(entry[1] - time.time()))


def incr(key: str, ttl_seconds: int) -> int:
    """Increment a counter, setting its TTL on the first hit (fixed window).

    Limits FAIL CLOSED (23.2b): if Redis errors, the count comes from this
    instance's in-memory window instead of 0. Per-instance limiting is weaker
    than shared limiting but infinitely better than none — the 2026-09-15
    incident (Upstash DB deleted → every request counted as 0 → no limit at all)
    is exactly what this prevents. Locks stay fail-open (see acquire_lock)."""
    if using_redis():
        try:
            # EXPIRE ... NX only sets the TTL when the key has none, so the window
            # starts on the first request and doesn't slide on every hit.
            res = _pipeline([["INCR", key], ["EXPIRE", key, str(ttl_seconds), "NX"]])
            return int(res[0])
        except Exception:
            return _mem_incr(key, ttl_seconds)
    return _mem_incr(key, ttl_seconds)


def ttl(key: str) -> int:
    """Seconds until `key` expires (for Retry-After). -1 if unknown/no expiry.
    Falls back to the in-memory window when Redis errors (pairs with incr)."""
    if using_redis():
        try:
            v = _command("TTL", key)
            return int(v) if v is not None else -1
        except Exception:
            return _mem_ttl(key)
    return _mem_ttl(key)


def ping() -> bool:
    """True if the configured Redis answers PING (False when unreachable or when
    running on the in-memory fallback). For startup/health diagnostics."""
    if not using_redis():
        return False
    try:
        return str(_command("PING")).upper() == "PONG"
    except Exception:
        return False


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


def acquire_lock(key: str, ttl_seconds: int) -> Optional[str]:
    """Best-effort distributed lock via SET NX EX. Returns the OWNER TOKEN when
    acquired, else None. The token makes the lock owned (23.3c): heartbeat and
    release only affect a lock this caller still holds, so a stalled build can no
    longer extend or delete the lock of the build that replaced it.
    Fail-open: grants the lock if Redis errored (better a rare double-build than a
    deadlocked request)."""
    token = uuid.uuid4().hex
    if using_redis():
        try:
            return token if _command("SET", key, token, "NX", "EX", ttl_seconds) == "OK" else None
        except Exception:
            return token
    with _mem_lock:
        if _mem_alive(_mem.get(key)):
            return None
        _mem[key] = [token, time.time() + ttl_seconds]
        return token


def refresh_lock(key: str, ttl_seconds: int, token: Optional[str] = None) -> bool:
    """Heartbeat: extend the TTL only while `token` still owns the lock. Returns
    False when the lock was lost (expired, or taken over by another build) — the
    caller should stop, since another build now owns this snapshot.
    `token=None` keeps the old unconditional behaviour for non-owned locks."""
    if token is None:
        set(key, "1", ttl_seconds)
        return True
    if using_redis():
        try:
            # XX = only if it already exists; combined with the value check below
            # this is a compare-and-extend (no Lua needed on the REST API).
            if _command("GET", key) != token:
                return False
            return _command("SET", key, token, "XX", "EX", ttl_seconds) == "OK"
        except Exception:
            return True   # fail-open: a cache blip shouldn't kill a live build
    with _mem_lock:
        entry = _mem.get(key)
        if not _mem_alive(entry) or entry[0] != token:
            return False
        entry[1] = time.time() + ttl_seconds
        return True


def release_lock(key: str, token: Optional[str] = None) -> None:
    """Release the lock — only if `token` still owns it (compare-and-delete)."""
    if token is None:
        delete(key)
        return
    if using_redis():
        try:
            if _command("GET", key) == token:
                delete(key)
        except Exception:
            pass
        return
    with _mem_lock:
        entry = _mem.get(key)
        if entry is not None and entry[0] == token:
            _mem.pop(key, None)
