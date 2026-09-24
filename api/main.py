"""FastAPI backend — exposes the SAME agent over HTTP for a React frontend.

The agent does not change; this just wraps run() / make_chart() / ensure_snapshot()
in endpoints. ALL logic stays in agent/ and data_layer/ — keep this file thin.

Run locally:
    uvicorn api.main:app --reload --port 8000
Then open http://localhost:8000/docs for the auto-generated API.
"""
import hashlib
import hmac
import json
import re
import urllib.parse
from pathlib import Path
from typing import Optional

import requests
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, field_validator

from config import (
    CORS_ORIGINS, CHART_TTL_HOURS, CHART_MAX_FILES, missing_secrets,
    RATE_LIMIT_ASK_PER_MIN, RATE_LIMIT_ASK_PER_DAY, RATE_LIMIT_SESSION_PER_MIN,
    RATE_LIMIT_CHART_PER_MIN, RATE_LIMIT_CHART_PER_DAY, RATE_LIMIT_READ_PER_MIN,
    RATE_LIMIT_STATUS_PER_MIN, MAX_QUESTION_CHARS, MAX_HISTORY_TURNS,
    MAX_HISTORY_ANSWER_CHARS,
    SNAPSHOT_TTL_DAYS, PUBLIC_API_URL, FRONTEND_URL, ANSWER_CACHE_TTL_SECONDS,
    SNAPSHOT_WAIT_MAX, AUTH_SECRET, AUTH_TOKEN_TTL_DAYS,
    DEMO_STEAM_ID, DEMO_ALIAS, DEMO_DISPLAY_NAME, DEMO_ASK_PER_DAY,
)
from agent import run, run_stream, make_chart, distill_memory, fast_answer
from data_layer import steam_client
from data_layer import storage
from data_layer import cache
from data_layer import db
from data_layer.library import build_library, header_stats, next_plan

import time
from data_layer.resolver import resolve_steam_id, SteamResolveError
from data_layer.snapshot import (
    ensure_snapshot, has_snapshot, load_frames, clear_snapshot, PrivateProfileError,
    snapshot_version, is_private_owned_payload, PRIVATE_PROFILE_MSG,
    snapshot_age_days, snapshot_meta, DEFAULT_MAX_AGE_DAYS,
)

import threading
from concurrent.futures import ThreadPoolExecutor

# Bounded background work (23.2f): an unbounded Thread per request let a burst of
# /session or /ask calls spawn arbitrarily many builds / Flash calls on a 512MB box.
# Excess work QUEUES — a queued build reports queued:true to the poller, which
# pauses its stall timer (a build waiting for a worker is not a stuck build).
_BUILD_POOL = ThreadPoolExecutor(max_workers=3, thread_name_prefix="build")
_MEMORY_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="memory")

# A SteamID64 is exactly 17 digits (23.2d). Every id that reaches a filesystem
# path, a bucket key, a DB filter or the sandbox argv is checked against this.
_STEAM_ID64_RE = re.compile(r"^\d{17}$")


def is_demo(steam_id: Optional[str]) -> bool:
    return bool(DEMO_STEAM_ID) and steam_id == DEMO_ALIAS


DEMO_OFF_MSG = ("The demo isn't available right now — sign in through Steam or "
                "paste your profile link instead.")


def _sid(steam_id: str) -> str:
    """Validate a client-supplied SteamID64 (or the demo alias) and return the id
    to USE server-side. The demo alias maps to DEMO_STEAM_ID here so the real
    account never has to appear in the browser, the URL or the network tab."""
    if is_demo(steam_id):
        return DEMO_STEAM_ID
    if not isinstance(steam_id, str) or not _STEAM_ID64_RE.match(steam_id):
        raise HTTPException(status_code=400, detail="Invalid Steam ID (expected a 17-digit SteamID64).")
    return steam_id


def _anonymize(summary: dict, steam_id: Optional[str]) -> dict:
    """Strip the demo profile's identity from anything we send to a browser."""
    if not is_demo(steam_id):
        return summary
    out = dict(summary)
    out.update(steam_id=DEMO_ALIAS, persona=DEMO_DISPLAY_NAME, avatar="")
    return out


_CHARTS_DIR = Path(__file__).parent.parent / "data" / "charts"
_CHARTS_DIR.mkdir(parents=True, exist_ok=True)


def _sweep_snapshots() -> int:
    """GC per-user snapshots not rebuilt in SNAPSHOT_TTL_DAYS (blob + DB row +
    local cache); they regenerate on the user's next visit. Identity + usage logs
    are kept. Needs the DB (uses snapshot.built_at), so it's a no-op without it.
    Best-effort — never raises into a request. Returns the number swept."""
    if not db.using_db():
        return 0
    from datetime import datetime, timezone, timedelta
    cutoff = (datetime.now(timezone.utc) - timedelta(days=SNAPSHOT_TTL_DAYS)).isoformat()
    swept = 0
    for sid in db.stale_snapshots(cutoff):
        try:
            clear_snapshot(sid)       # blob (object storage) + local cache
            db.delete_snapshot(sid)   # metadata row
            swept += 1
        except Exception:
            pass
    return swept


def _sweep_charts() -> int:
    """Delete stale/excess charts so the chart store stays bounded.

    Charts are disposable (regenerable from the snapshot), so this is safe: drop
    anything older than CHART_TTL_HOURS, then cap to the newest CHART_MAX_FILES.
    Delegates to the storage layer so it sweeps the local dir in dev and the
    Supabase 'charts' bucket in prod (Supabase Storage has no native lifecycle
    expiry). Best-effort — never raises into a request."""
    return storage.sweep("charts", CHART_TTL_HOURS * 3600, CHART_MAX_FILES)

# Fields that are large or internal — stripped from /ask responses.
_DROP_FIELDS = {"schema", "history", "with_insight", "_token_sink"}

app = FastAPI(title="Hundo API", version="1.0", description="Steam achievement AI analyst")


# ── Per-IP rate limiting (Step 15.3) ──────────────────────────────────────────
# Registered BEFORE CORS on purpose: middleware added later is outermost, so CORS
# must be added last to wrap our 429 responses with CORS headers — otherwise the
# browser sees a CORS error instead of the friendly "slow down" message.

def _client_ip(request: Request) -> str:
    """Real client IP. On Render/Vercel we sit behind a proxy, so the original
    client is the first hop in X-Forwarded-For.
    Spoof-checked LIVE 2026-09-19 (23.2a): sending `X-Forwarded-For: 9.9.9.9` (and
    a two-hop variant) to the deployed API still yielded the real client IP —
    Render's edge REPLACES the client-supplied header, so the first hop is
    trustworthy there. Do NOT switch to the rightmost hop: behind extra internal
    proxies that is an internal IP and would put every user in ONE bucket.
    Re-run that curl test if the hosting/proxy setup ever changes."""
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


# path -> (bucket, [(window-label, seconds, limit), ...]). /ask and /ask/stream
# share one bucket so streaming isn't a way around the limit.
_ASK_WINDOWS = [("min", 60, RATE_LIMIT_ASK_PER_MIN), ("day", 86400, RATE_LIMIT_ASK_PER_DAY)]
_RATE_RULES = {
    "/ask": ("ask", _ASK_WINDOWS),
    "/ask/stream": ("ask", _ASK_WINDOWS),
    "/session": ("session", [("min", 60, RATE_LIMIT_SESSION_PER_MIN)]),
    # A refresh is a full library re-fetch — stricter than /session on purpose.
    "/session/refresh": ("refresh", [("min", 60, 2), ("day", 86400, 20)]),
    "/session/status": ("status", [("min", 60, RATE_LIMIT_STATUS_PER_MIN)]),
    "/chart": ("chart", [("min", 60, RATE_LIMIT_CHART_PER_MIN),
                         ("day", 86400, RATE_LIMIT_CHART_PER_DAY)]),
    "/library": ("library", [("min", 60, RATE_LIMIT_READ_PER_MIN)]),
    "/popular": ("popular", [("min", 60, RATE_LIMIT_READ_PER_MIN)]),
    "/memory": ("memory", [("min", 60, RATE_LIMIT_READ_PER_MIN)]),
    "/demo/plan": ("demo_plan", [("min", 60, RATE_LIMIT_READ_PER_MIN)]),
}


@app.middleware("http")
async def rate_limit(request: Request, call_next):
    rule = _RATE_RULES.get(request.url.path)
    if rule:
        bucket, windows = rule
        ip = _client_ip(request)
        for label, seconds, limit in windows:
            key = f"rl:{bucket}:{label}:{ip}"
            if cache.incr(key, seconds) > limit:
                retry = max(cache.ttl(key), 1)
                return JSONResponse(
                    status_code=429,
                    content={"detail": f"Rate limit reached ({limit} per {label}). Try again in ~{retry}s."},
                    headers={"Retry-After": str(retry)},
                )
    return await call_next(request)


app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)
# Serve generated chart PNGs as static files (e.g. /charts/<hash>.png).
app.mount("/charts", StaticFiles(directory=str(_CHARTS_DIR)), name="charts")


def _chart_url(path: Optional[str]) -> Optional[str]:
    """Turn a freshly-generated local chart PNG into a browser-reachable URL.

    The sandbox always writes the PNG to local disk (it has no network). In
    Supabase mode we upload those bytes to the public 'charts' bucket and return
    its public URL, then drop the local temp; in dev we just serve it from the
    /charts static mount."""
    if not path:
        return None
    name = Path(path).name
    if storage.using_supabase():
        try:
            storage.put("charts", name, Path(path).read_bytes(), content_type="image/png")
        except Exception:
            return None
        try:
            Path(path).unlink()  # local temp no longer needed
        except OSError:
            pass
        return storage.public_url("charts", name)
    return f"/charts/{name}"


def _serialize(result: dict, demo: bool = False) -> dict:
    """Strip heavy/internal fields and turn a local chart_path into a chart_url.
    For the public demo, the real SteamID is replaced by the alias so it never
    reaches a browser (the result echo is posted back to /chart)."""
    out = {k: v for k, v in result.items() if k not in _DROP_FIELDS}
    out["chart_url"] = _chart_url(out.pop("chart_path", None))
    if demo and out.get("steam_id"):
        out["steam_id"] = DEMO_ALIAS
    return out


# ── Answer cache (Step 19.4) ──────────────────────────────────────────────────
# Identical question + same user + same snapshot build + same memory → serve the
# stored result from Redis, zero LLM cost. Sits BEHIND the fast-path (which is
# already free) and only in api/ — run() stays pure, eval untouched. Fail-open
# like everything else on the cache: any miss/hiccup just runs the agent.

# Never cache: small-talk (no route), clarifying questions, or chitchat — they're
# either dialogue-dependent or already one cheap Flash call.
_UNCACHEABLE_ROUTES = {"", "chitchat", "clarify"}


def _norm_question(q: str) -> str:
    return re.sub(r"\s+", " ", (q or "").strip().lower()).strip(" ?!.")


def _memory_fingerprint(memory: str) -> str:
    """A COARSE fingerprint of the user's memory (23.6g).

    The key used to hash the memory TEXT, but the distill thread rewrites memory
    after every /ask — so an answer was stored under the pre-turn memory and
    looked up under the post-turn one. Measured live: the same question ran the
    full agent TWICE and only hit on the third ask. Fingerprinting the memory's
    durable SHAPE (its bullet lines, normalized and sorted) means cosmetic
    rewording no longer misses, while a real change in what we remember still
    invalidates the personalized answers."""
    lines = sorted(
        re.sub(r"\s+", " ", ln.strip(" -•\t").lower()).strip()
        for ln in (memory or "").splitlines()
        if ln.strip(" -•\t")
    )
    return hashlib.sha1("|".join(lines).encode()).hexdigest()[:12]


def _answer_cache_key(req: "AskReq", memory: str, steam_id: Optional[str] = None) -> Optional[str]:
    """Cache key, or None when this request shouldn't touch the cache: caching is
    disabled, it's a follow-up (history changes the answer), or the snapshot has
    no local build marker yet. The memory FINGERPRINT (not its text) is hashed in,
    so a personalization change misses but a reworded one doesn't (23.6g)."""
    if ANSWER_CACHE_TTL_SECONDS <= 0 or req.history:
        return None
    # `steam_id` is the RESOLVED id (the demo alias maps to a real one) — keying
    # on the alias would look up a snapshot that doesn't exist and silently
    # disable caching for exactly the traffic it's meant to subsidize.
    resolved = steam_id or req.steam_id
    sid = resolved or "default"
    ver = snapshot_version(resolved) if resolved else snapshot_version()
    if not ver:
        return None
    h = hashlib.sha1(
        f"{_norm_question(req.question)}|{_memory_fingerprint(memory)}|{int(req.with_insight)}".encode()
    ).hexdigest()
    return f"ans:{sid}:{ver}:{h}"


def _answer_cache_get(key: Optional[str]) -> Optional[dict]:
    if not key:
        return None
    raw = cache.get(key)
    if not raw:
        return None
    try:
        out = json.loads(raw)
    except (ValueError, TypeError):
        return None
    out.pop("llm_usage", None)  # those tokens were spent by the ORIGINAL request
    out["cached"] = True
    return out


def _answer_cache_put(key: Optional[str], serialized: dict) -> None:
    """Store a serialized /ask response — only clean, grounded answers."""
    if not key:
        return
    route = serialized.get("route") or ""
    if route in _UNCACHEABLE_ROUTES or serialized.get("error") or not serialized.get("answer"):
        return
    try:
        cache.set(key, json.dumps(serialized), ANSWER_CACHE_TTL_SECONDS)
    except (TypeError, ValueError):
        pass  # non-serializable payload → just don't cache it


# ── Request models ────────────────────────────────────────────────────────────

class SessionReq(BaseModel):
    profile: str                       # alias, custom URL, profile link, or SteamID64


class AskReq(BaseModel):
    question: str
    steam_id: Optional[str] = None     # None → server's default STEAM_ID
    history: Optional[list[dict]] = None
    with_insight: bool = True

    @field_validator("history")
    @classmethod
    def _trim_history(cls, v):
        # 23.2e/22.4: keep the last N turns, clip each — trimmed, never rejected.
        if not v:
            return v
        out = []
        for t in v[-MAX_HISTORY_TURNS:]:
            if not isinstance(t, dict):
                continue
            q = str(t.get("question") or "")[:MAX_QUESTION_CHARS]
            a = str(t.get("answer") or "")
            if len(a) > MAX_HISTORY_ANSWER_CHARS:
                a = a[:MAX_HISTORY_ANSWER_CHARS] + " …"
            out.append({"question": q, "answer": a})
        return out


def _demo_quota_ok() -> bool:
    """Shared daily budget for AGENT questions asked from the public demo (20.0a).
    Cached + fast-path answers never reach here, so only genuinely new questions
    spend it. 0 disables the cap."""
    if DEMO_ASK_PER_DAY <= 0:
        return True
    return cache.incr("demo:ask:day", 86400) <= DEMO_ASK_PER_DAY


_DEMO_LIMIT_MSG = ("The demo has answered its questions for today — sign in through Steam "
                   "to ask about your own library (no limit).")


def _resolve_ask(req: AskReq) -> tuple[str, bool]:
    """(server-side steam_id, is_demo) for an /ask request."""
    demo = is_demo(req.steam_id)
    return (DEMO_STEAM_ID if demo else req.steam_id), demo


def _check_question(req: AskReq) -> None:
    """Reject a bad steam_id or an empty / over-long question with a readable 400
    (a pydantic 422 carries a detail LIST, which the frontend can't display)."""
    if req.steam_id is not None:
        _sid(req.steam_id)
    if not req.question.strip():
        raise HTTPException(status_code=400, detail="Please type a question.")
    if len(req.question) > MAX_QUESTION_CHARS:
        raise HTTPException(status_code=400, detail=(
            f"That question is too long ({len(req.question)} characters) — "
            f"please keep it under {MAX_QUESTION_CHARS}."))


_MAX_CHART_RESULT_BYTES = 64_000   # a real /ask echo is a few KB


class RefreshReq(BaseModel):
    steam_id: str


class ChartReq(BaseModel):
    result: dict                       # a prior /ask response (echoed back)


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.get("/health")
def health(request: Request, debug: int = 0):
    """Cheap liveness for the platform's frequent health checks. Pass ?debug=1 for
    a live cache round-trip + the client IP this host sees (diagnostics) — kept off
    by default so routine checks don't burn the Upstash command quota."""
    # `demo` tells the landing page whether the "Try the demo" button is live.
    out = {"status": "ok", "missing_secrets": missing_secrets(), "demo": bool(DEMO_STEAM_ID)}
    if debug:
        redis_ok = None
        if cache.using_redis():
            try:
                cache.set("health:ping", "1", 30)
                redis_ok = cache.get("health:ping") == "1"
            except Exception:
                redis_ok = False
        out.update(redis=cache.using_redis(), redis_ok=redis_ok, client_ip=_client_ip(request))
    return out


# ── Steam sign-in (OpenID 2.0, convenience) ───────────────────────────────────
# Identity only — proves the SteamID64, grants no API scope (private profiles still
# return nothing). We pass the VERIFIED id back to the frontend via ?steam_id= and
# it auto-loads; manual ID entry still works. No sessions/cookies (data is public).
_STEAM_OPENID = "https://steamcommunity.com/openid/login"
_STEAMID_RE = re.compile(r"https?://steamcommunity\.com/openid/id/(\d+)")


@app.get("/auth/steam/login")
def steam_login():
    """Redirect the browser to Steam's OpenID sign-in page."""
    params = {
        "openid.ns": "http://specs.openid.net/auth/2.0",
        "openid.mode": "checkid_setup",
        "openid.return_to": f"{PUBLIC_API_URL}/auth/steam/return",
        "openid.realm": PUBLIC_API_URL,
        "openid.identity": "http://specs.openid.net/auth/2.0/identifier_select",
        "openid.claimed_id": "http://specs.openid.net/auth/2.0/identifier_select",
    }
    return RedirectResponse(f"{_STEAM_OPENID}?{urllib.parse.urlencode(params)}")


@app.get("/auth/steam/return")
def steam_return(request: Request):
    """Steam redirects here after sign-in. Verify the response with Steam, extract
    the SteamID64, and bounce back to the frontend with ?steam_id= (or ?login_error=1)."""
    # Echo the params back to Steam with mode=check_authentication to verify.
    params = dict(request.query_params)
    params["openid.mode"] = "check_authentication"
    steam_id = None
    try:
        r = requests.post(_STEAM_OPENID, data=params, timeout=15)
        if "is_valid:true" in r.text:
            m = _STEAMID_RE.search(request.query_params.get("openid.claimed_id", ""))
            steam_id = m.group(1) if m else None
    except requests.RequestException:
        steam_id = None
    if steam_id:
        # Hand the frontend a signed token for the id Steam just verified (23.4b).
        token = issue_auth_token(steam_id)
        suffix = f"&auth={urllib.parse.quote(token)}" if token else ""
        return RedirectResponse(f"{FRONTEND_URL}/?steam_id={steam_id}{suffix}")
    return RedirectResponse(f"{FRONTEND_URL}/?login_error=1")


# ── Verified identity (23.4b) ─────────────────────────────────────────────────
# Steam OpenID proves a SteamID64. We mint a signed, expiring token for it and
# the frontend sends it back as X-Hundo-Auth. It gates MEMORY only: everything
# else here is public Steam data that anyone may request for any id. Without a
# valid token the agent still answers — it just doesn't read or write the
# per-user memory, so a stranger can't poison or wipe what we remember about you.

_AUTH_HEADER = "x-hundo-auth"


def issue_auth_token(steam_id: str) -> str:
    """Sign `steam_id` with an expiry. Empty string when no secret is configured."""
    if not AUTH_SECRET:
        return ""
    exp = int(time.time() + AUTH_TOKEN_TTL_DAYS * 86400)
    body = f"{steam_id}.{exp}"
    sig = hmac.new(AUTH_SECRET.encode(), body.encode(), hashlib.sha256).hexdigest()[:32]
    return f"{body}.{sig}"


def verified_steam_id(request: Request) -> Optional[str]:
    """The SteamID64 this request PROVED it owns, or None. Constant-time compare."""
    token = request.headers.get(_AUTH_HEADER, "")
    if not (token and AUTH_SECRET):
        return None
    try:
        sid, exp_s, sig = token.rsplit(".", 2)
        if int(exp_s) < time.time():
            return None
    except ValueError:
        return None
    expect = hmac.new(AUTH_SECRET.encode(), f"{sid}.{exp_s}".encode(),
                      hashlib.sha256).hexdigest()[:32]
    return sid if hmac.compare_digest(expect, sig) and _STEAM_ID64_RE.match(sid) else None


def _memory_allowed(request: Request, steam_id: Optional[str]) -> bool:
    """True when this caller PROVED it owns `steam_id` (so memory may be used)."""
    return bool(steam_id) and verified_steam_id(request) == steam_id


# ── Async snapshot build (Step 15.4) ──────────────────────────────────────────
# /session no longer blocks on the (possibly minutes-long) build — that exceeds
# the hosting edge timeout for big libraries. Instead it kicks the build off in a
# background thread and returns "building"; the client polls /session/status for
# live progress. Status + progress live in the cache (Redis in prod) so they're
# correct even if the host scales to multiple instances.
_STATUS_TTL = 1800  # 30 min — comfortably longer than any build


def _status_key(sid: str) -> str:
    return f"snap:status:{sid}"


def _progress_key(sid: str) -> str:
    return f"snap:progress:{sid}"


# Build status in the cache: "queued:<epoch>" → "building" → "ready" | "failed:<msg>".
# "queued" = submitted to _BUILD_POOL but no worker has picked it up yet. It used
# to be indistinguishable from a dead build (both "building 0/0"), so the client's
# stall timer gave up on healthy builds that were just waiting (code review
# 2026-09-24). The enqueue time rides along so a queue that never drains — the
# process died, e.g. a redeploy — turns into a retry message, not an endless wait.
def _queued_status() -> str:
    return f"queued:{int(time.time())}"


def _queued_at(status: str) -> Optional[float]:
    if not status.startswith("queued"):
        return None
    try:
        return float(status.split(":", 1)[1])
    except (IndexError, ValueError):
        return 0.0  # unparseable → treat as ancient → retry message


def _in_flight(status: Optional[str]) -> bool:
    return status == "building" or _queued_at(status or "") is not None


def _session_summary(steam_id: str) -> dict:
    """Headline profile stats for the UI header (assumes the snapshot is ready)."""
    stats = header_stats(load_frames(steam_id))
    summary = {}
    try:
        summary = steam_client.get_player_summary(steam_id)
    except Exception:
        pass
    age = snapshot_age_days(steam_id)
    return {
        "steam_id": steam_id,
        "persona": summary.get("personaname", ""),
        "avatar": summary.get("avatarfull", ""),
        # Freshness (23.3b) so the UI can show "updated N ago" + a refresh button.
        "built_at": snapshot_meta(steam_id).get("built_at"),
        "age_days": round(age, 3) if age is not None else None,
        "stale": bool(age is not None and age > DEFAULT_MAX_AGE_DAYS),
        **stats,
    }


def _refresh_in_background(steam_id: str) -> bool:
    """Kick off a rebuild for a user who already HAS a snapshot (23.3b). The old
    snapshot keeps serving until the new one lands (stale-while-revalidate), so
    nothing blocks and a Steam outage can't take a working profile away. Returns
    False when a build is already running for this user."""
    if _in_flight(cache.get(_status_key(steam_id))):
        return False
    cache.set(_status_key(steam_id), _queued_status(), _STATUS_TTL)
    _BUILD_POOL.submit(_run_build, steam_id, None, True)
    return True


def _run_build(steam_id: str, owned: Optional[dict] = None, force: bool = False) -> None:
    """Background worker: build the snapshot, writing progress to the cache. The
    build-lock inside ensure_snapshot dedupes concurrent builds for the same user.
    Status is mirrored to the DB row (queued → building → ready/failed) so it is
    visible even when Redis is unavailable (2026-09-15 incident)."""
    # A worker picked it up: no longer queued. The DB row only tracks FIRST builds
    # (a refresh keeps the previous good row until it finishes).
    cache.set(_status_key(steam_id), "building", _STATUS_TTL)
    cache.set(_progress_key(steam_id), "0/0", _STATUS_TTL)
    if not force:
        db.upsert_snapshot(steam_id, status="building")

    def cb(done: int, total: int) -> None:
        cache.set(_progress_key(steam_id), f"{done}/{total}", _STATUS_TTL)

    try:
        # max_age_days was dead config until 23.3b — a snapshot older than
        # DEFAULT_MAX_AGE_DAYS is now rebuilt instead of being cached forever.
        ensure_snapshot(steam_id, max_age_days=DEFAULT_MAX_AGE_DAYS,
                        progress_cb=cb, owned=owned, force=force)
        cache.set(_status_key(steam_id), "ready", _STATUS_TTL)
        _record_snapshot_meta(steam_id)
    except PrivateProfileError as e:
        cache.set(_status_key(steam_id), f"failed:{e}", _STATUS_TTL)
        db.upsert_snapshot(steam_id, status="failed", error=str(e))
    except Exception as e:
        cache.set(_status_key(steam_id), f"failed:Couldn't load profile: {e}", _STATUS_TTL)
        db.upsert_snapshot(steam_id, status="failed", error=f"Couldn't load profile: {e}")


_RETRY_HINT = ("The profile build didn't finish (the server restarted or the build "
               "status expired). Please load the profile again.")


def _row_age_seconds(row: Optional[dict]) -> Optional[float]:
    """Seconds since the snapshot row's built_at (None if absent/unparseable)."""
    from datetime import datetime, timezone
    try:
        ts = datetime.fromisoformat(str(row["built_at"]).replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - ts).total_seconds()
    except (KeyError, TypeError, ValueError):
        return None


def _record_snapshot_meta(steam_id: str) -> None:
    """Write snapshot header stats to the DB after a successful build (best-effort)."""
    try:
        frames = load_frames(steam_id)
        ach, pu, games = frames["achievements"], frames["player_unlocks"], frames["games"]
        location = f"snapshots/{steam_id}/" if storage.using_supabase() else f"data/snapshot/{steam_id}/"
        db.upsert_snapshot(
            steam_id,
            status="ready",
            games=int(len(games)),
            total_achievements=int(len(ach)),
            total_unlocked=int(pu["achieved"].sum()),
            location=location,
        )
    except Exception:
        pass


@app.post("/session")
def session(req: SessionReq):
    """Resolve a profile and return its summary if the snapshot is ready; otherwise
    start the build in the background and return {status:"building"}. The client
    then polls /session/status. Resolution stays synchronous (one fast Steam call)."""
    # With no demo configured the alias must NOT fall through to vanity
    # resolution: "demo" is a real Steam custom URL, so the demo button would load
    # a stranger's public profile, un-anonymized (code review 2026-09-24).
    if req.profile == DEMO_ALIAS and not DEMO_STEAM_ID:
        raise HTTPException(status_code=404, detail=DEMO_OFF_MSG)
    _sweep_charts()      # opportunistic cleanup so long-running servers stay bounded
    _sweep_snapshots()   # GC snapshots not rebuilt in SNAPSHOT_TTL_DAYS (DB-driven)
    demo = is_demo(req.profile)
    if demo:
        steam_id = DEMO_STEAM_ID          # alias → real id, server-side only
    else:
        try:
            steam_id = resolve_steam_id(req.profile)
        except SteamResolveError as e:
            raise HTTPException(status_code=404, detail=str(e))
        _sid(steam_id)   # resolver output reaches paths/keys — never trust it blindly

    if has_snapshot(steam_id):
        cache.set(_status_key(steam_id), "ready", _STATUS_TTL)
        summ = _session_summary(steam_id)
        db.upsert_user(steam_id, req.profile, summ["persona"], summ["avatar"])
        # Stale snapshot → serve it NOW and refresh behind the scenes (23.3b).
        if summ.get("stale"):
            summ["refreshing"] = _refresh_in_background(steam_id)
        return {"status": "ready", **_anonymize(summ, req.profile)}

    # Not built yet → record identity, then check privacy UP-FRONT with one Steam
    # call (reused by the build): a private profile fails instantly with the real
    # message instead of an async build whose failure was invisible when Redis
    # was down (2026-09-15 incident). A transient Steam error just skips the check.
    db.upsert_user(steam_id, req.profile)
    owned = None
    try:
        owned = steam_client.get_owned_games(steam_id)
    except Exception:
        owned = None
    if owned is not None and is_private_owned_payload(owned):
        db.upsert_snapshot(steam_id, status="failed", error=PRIVATE_PROFILE_MSG)
        raise HTTPException(status_code=403, detail=PRIVATE_PROFILE_MSG)

    # Launch the background build and tell the client to poll. It starts QUEUED
    # (the worker flips it to "building" when it picks it up). The DB row is
    # written SYNCHRONOUSLY here (not in the thread) so the very first poll can
    # already see it even if the cache is unavailable.
    db.upsert_snapshot(steam_id, status="queued")
    cache.set(_status_key(steam_id), _queued_status(), _STATUS_TTL)
    cache.set(_progress_key(steam_id), "0/0", _STATUS_TTL)
    _BUILD_POOL.submit(_run_build, steam_id, owned)
    return {"status": "building", "steam_id": DEMO_ALIAS if demo else steam_id}


@app.post("/session/refresh")
def session_refresh(req: "RefreshReq"):
    """Force a rebuild of an existing snapshot (23.3b). The current snapshot keeps
    serving while the new one builds, so this never leaves the user with nothing."""
    steam_id = _sid(req.steam_id)
    if not has_snapshot(steam_id):
        raise HTTPException(status_code=404, detail="No snapshot yet — load the profile first.")
    started = _refresh_in_background(steam_id)
    return {"status": "refreshing" if started else "already_building", "steam_id": steam_id}


@app.get("/library")
def library(steam_id: str):
    """The full trophy-case dataset (profile, cards, games, curator highlights) for
    a built profile. Read-only transform over the snapshot — see data_layer/library."""
    alias, steam_id = steam_id, _sid(steam_id)
    if not has_snapshot(steam_id):
        raise HTTPException(status_code=404, detail="No snapshot yet — load the profile first.")
    lib = build_library(steam_id)
    if is_demo(alias):
        # Demo: keep the real library (that's the point) but not the identity.
        lib["profile"]["name"] = DEMO_DISPLAY_NAME
        lib["profile"]["avatar"] = ""
        return lib
    try:
        s = steam_client.get_player_summary(steam_id)
        lib["profile"]["name"] = s.get("personaname", "") or steam_id
        lib["profile"]["avatar"] = s.get("avatarfull", "")
    except Exception:
        lib["profile"]["name"] = lib["profile"]["name"] or steam_id
    return lib


DEMO_PLAN_TTL = 6 * 3600  # the demo snapshot only changes on its weekly rebuild


@app.get("/demo/plan")
def demo_plan():
    """The landing page's hero plan, computed from the demo library (17.12). The
    page renders a frozen copy instantly and swaps this in when it arrives, so a
    sleeping server costs nothing. Cached per snapshot version; names, descriptions
    and rarity only — the demo identity never appears."""
    if not DEMO_STEAM_ID or not has_snapshot(DEMO_STEAM_ID):
        raise HTTPException(status_code=404, detail="No demo library configured.")
    key = f"demo:plan:{snapshot_version(DEMO_STEAM_ID)}"
    cached = cache.get(key)
    if cached:
        try:
            return json.loads(cached)
        except Exception:
            pass
    plan = next_plan(build_library(DEMO_STEAM_ID))
    if plan is None:
        raise HTTPException(status_code=404, detail="Nothing left to plan in the demo library.")
    plan["built_at"] = snapshot_meta(DEMO_STEAM_ID).get("built_at")
    cache.set(key, json.dumps(plan), DEMO_PLAN_TTL)
    return plan


@app.get("/popular")
def popular(steam_id: str):
    """Currently most-played Steam games the user does NOT own — discovery picks for
    a fresh 100% (the unowned-roadmap path handles these). appdetails are cached
    per-app (shared across users) so this stays cheap."""
    steam_id = _sid(steam_id)
    from agent.search import cached_json
    owned: set[int] = set()
    if has_snapshot(steam_id):
        try:
            owned = {int(a) for a in load_frames(steam_id)["games"]["appid"].tolist()}
        except Exception:
            owned = set()
    out = []
    try:
        for appid in steam_client.get_most_played():
            if appid in owned:
                continue
            d = cached_json(f"appdetails:{appid}", lambda a=appid: steam_client.get_app_details(a))
            if not d or d.get("type") != "game" or not d.get("name"):
                continue
            out.append({"appid": appid, "name": d["name"]})
            if len(out) >= 12:
                break
    except Exception:
        pass
    return {"games": out}


@app.get("/session/status")
def session_status(steam_id: str):
    """Poll a build's progress. Returns ready (+ summary), building (+ progress),
    or failed (+ error). Cheap + fast — safe to poll every ~1.5s."""
    alias, steam_id = steam_id, _sid(steam_id)
    if has_snapshot(steam_id):
        summ = _session_summary(steam_id)
        db.upsert_user(steam_id, persona=summ["persona"], avatar=summ["avatar"])
        return {"status": "ready", **_anonymize(summ, alias)}

    status = cache.get(_status_key(steam_id)) or ""
    if status.startswith("failed:"):
        return {"status": "failed", "error": status[len("failed:"):]}

    # Waiting for a free build worker. `queued` tells the client this 0/0 is a
    # queue, not a stall; a queue that outlives SNAPSHOT_WAIT_MAX died with its
    # process and won't start on its own.
    queued_at = _queued_at(status)
    if queued_at is not None:
        if time.time() - queued_at > SNAPSHOT_WAIT_MAX:
            return {"status": "failed", "error": _RETRY_HINT}
        return {"status": "building", "queued": True,
                "progress": {"done": 0, "total": 0, "pct": 0}}

    prog = cache.get(_progress_key(steam_id)) or "0/0"
    try:
        done_s, total_s = prog.split("/")
        done, total = int(done_s), int(total_s)
    except ValueError:
        done, total = 0, 0

    # No cache status (Redis down / key expired) or no progress yet → consult the
    # DB copy of the status (written by /session and the build worker) so a
    # failure is NEVER invisible: previously a dead Redis meant every failed or
    # killed build showed "building 0/0" forever (2026-09-15 incident, 23.3c).
    if not status or (done, total) == (0, 0):
        row = db.get_snapshot(steam_id) or {}
        if row.get("status") == "failed":
            return {"status": "failed", "error": row.get("error") or _RETRY_HINT}
        if not status:
            age = _row_age_seconds(row)
            if row.get("status") in ("queued", "building") and age is not None and age < SNAPSHOT_WAIT_MAX:
                out = {"status": "building", "progress": {"done": 0, "total": 0, "pct": 0},
                       "note": "live progress unavailable"}
                if row.get("status") == "queued":
                    out["queued"] = True
                return out
            return {"status": "failed", "error": _RETRY_HINT}

    pct = int(done * 100 / total) if total else 0
    return {"status": "building", "progress": {"done": done, "total": total, "pct": pct}}


# ── Cross-session memory (Tier 2) ─────────────────────────────────────────────
# A per-user memory summary the agent remembers across visits. Loaded before each
# /ask and woven into the prompts; updated in the BACKGROUND afterwards (a cheap
# Flash merge) so it never adds latency. No-op without the DB.

def _memory_for(steam_id: Optional[str]) -> str:
    return db.get_memory(steam_id) if (steam_id and db.using_db()) else ""


def _update_memory_bg(steam_id: Optional[str], question: str, answer: Optional[str], current: str) -> None:
    """Distill + save the user's memory off the request path. Best-effort."""
    if not (steam_id and answer and db.using_db()):
        return
    def work():
        try:
            updated = distill_memory(current, question, answer)
            if updated and updated.strip() != (current or "").strip():
                db.save_memory(steam_id, updated)
        except Exception:
            pass
    _MEMORY_POOL.submit(work)


@app.get("/memory")
def memory_get(steam_id: str, request: Request):
    """What the agent remembers about this user (for transparency). Readable ONLY
    by a caller that signed in through Steam as this id — memory is derived from
    someone's conversations, so a public SteamID must not expose it (23.4b)."""
    steam_id = _sid(steam_id)
    if not _memory_allowed(request, steam_id):
        return {"memory": "", "verified": False}
    return {"memory": db.get_memory(steam_id), "verified": True}


@app.delete("/memory")
def memory_delete(steam_id: str, request: Request):
    """Wipe this user's remembered memory (verified owner only)."""
    steam_id = _sid(steam_id)
    if not _memory_allowed(request, steam_id):
        raise HTTPException(status_code=403, detail=(
            "Sign in through Steam to manage what Hundo remembers about this profile."))
    db.delete_memory(steam_id)
    return {"ok": True}


@app.post("/ask")
def ask(req: AskReq, request: Request):
    """Answer a question. Returns the agent result (answer, route, trace fields,
    chart_url or chart_pending). For chart_pending answers, the client then calls
    /chart with this same result (answer-first UX)."""
    _check_question(req)
    t0 = time.perf_counter()
    sid, demo = _resolve_ask(req)
    # Deterministic fast-path (19.3): common shapes answered straight from the
    # snapshot — no LLM, no memory read/update (nothing durable to distill).
    fast = fast_answer(req.question, sid)
    if fast:
        db.log_query(sid, req.question, fast.get("route"),
                     int((time.perf_counter() - t0) * 1000))
        return _serialize(fast, demo)
    # Memory is read/written ONLY for a caller that proved this identity via Steam
    # OpenID (23.4b) — otherwise anyone could poison or read it via a public id.
    mem_ok = _memory_allowed(request, sid) and not demo
    mem = _memory_for(sid) if mem_ok else ""
    # Answer cache (19.4): a repeat of a cached question skips the agent entirely.
    ckey = _answer_cache_key(req, mem, sid)
    hit = _answer_cache_get(ckey)
    if hit:
        db.log_query(sid, req.question, f"cached:{hit.get('route') or ''}",
                     int((time.perf_counter() - t0) * 1000))
        # No distill on a cache hit: the exchange is a repeat, so there is nothing
        # new to remember — and rewriting memory here would invalidate the very
        # cache entry we just served (23.6g).
        return hit
    if demo and not _demo_quota_ok():
        raise HTTPException(status_code=429, detail=_DEMO_LIMIT_MSG)
    result = run(
        req.question,
        steam_id=sid,
        history=req.history,
        with_insight=req.with_insight,
        memory=mem,
    )
    db.log_query(sid, req.question, result.get("route"),
                 int((time.perf_counter() - t0) * 1000), usage=result.get("llm_usage"))
    if mem_ok:
        _update_memory_bg(sid, req.question, result.get("answer"), mem)
    out = _serialize(result, demo)
    _answer_cache_put(ckey, out)
    return out


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


@app.post("/ask/stream")
def ask_stream(req: AskReq, request: Request):
    """Streaming variant of /ask (Server-Sent Events). Emits `progress` events as
    each agent node fires, then a final `result` event with the serialized payload.
    The client reads this as a stream (fetch + ReadableStream)."""
    _check_question(req)   # before the stream opens → a normal 400, not a broken SSE
    def gen():
        t0 = time.perf_counter()
        sid, demo = _resolve_ask(req)
        # Deterministic fast-path (19.3): instant single result event, no LLM.
        fast = fast_answer(req.question, sid)
        if fast:
            db.log_query(sid, req.question, fast.get("route"),
                         int((time.perf_counter() - t0) * 1000))
            yield _sse("result", _serialize(fast, demo))
            return
        mem_ok = _memory_allowed(request, sid) and not demo
        mem = _memory_for(sid) if mem_ok else ""
        # Answer cache (19.4): a hit is a single instant result event, no agent.
        ckey = _answer_cache_key(req, mem, sid)
        hit = _answer_cache_get(ckey)
        if hit:
            db.log_query(sid, req.question, f"cached:{hit.get('route') or ''}",
                         int((time.perf_counter() - t0) * 1000))
            # No distill on a cache hit (23.6g) — see /ask.
            yield _sse("result", hit)
            return
        if demo and not _demo_quota_ok():
            yield _sse("result", {"answer": f"⚠️ {_DEMO_LIMIT_MSG}", "done": True})
            return
        for kind, payload in run_stream(
            req.question,
            steam_id=sid,
            history=req.history,
            with_insight=req.with_insight,
            memory=mem,
        ):
            if kind == "progress":
                yield _sse("progress", {"node": payload})
            elif kind == "token":
                yield _sse("token", {"text": payload})
            else:
                db.log_query(sid, req.question, payload.get("route"),
                             int((time.perf_counter() - t0) * 1000),
                             usage=payload.get("llm_usage"))
                if mem_ok:
                    _update_memory_bg(sid, req.question, payload.get("answer"), mem)
                out = _serialize(payload, demo)
                _answer_cache_put(ckey, out)
                yield _sse("result", out)

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.post("/chart")
def chart(req: ChartReq):
    """Second-pass chart generation for a prior /ask result. Returns a JSON
    chart SPEC the frontend renders itself (19.2). chart_url is kept (always
    null) so an older cached frontend fails soft during a deploy overlap."""
    # The echoed result feeds a Pro prompt + a sandbox run → bound it and check
    # the id it carries (load_frames(result["steam_id"]) touches the filesystem).
    if len(json.dumps(req.result, default=str)) > _MAX_CHART_RESULT_BYTES:
        raise HTTPException(status_code=413, detail="Chart request too large.")
    if req.result.get("steam_id") is not None:
        # Map the demo alias back to the real id so the chart can load frames.
        req.result["steam_id"] = _sid(req.result["steam_id"])
    return {"chart_spec": make_chart(req.result), "chart_url": None}


# Warm up the LLM connection on boot (first TLS handshake + model spin-up is the
# slow part), so a user's FIRST message doesn't pay the cold-start cost.
def _warmup():
    try:
        from agent.llm import call_llm
        from config import DEEPSEEK_MODEL_FLASH
        call_llm("hi", model=DEEPSEEK_MODEL_FLASH, system="Reply with OK.")
    except Exception:
        pass


# Startup secret validation — fail loudly in the logs if required secrets are
# missing, instead of surfacing a cryptic 403/502 deep inside a request later.
_missing = missing_secrets()
if _missing:
    print(f"[startup] WARNING: missing required secrets: {', '.join(_missing)} — "
          "related features will fail until set.")
else:
    print("[startup] all required secrets present.")
if cache.using_redis():
    _redis_state = "upstash" if cache.ping() else (
        "upstash UNREACHABLE — check UPSTASH_REDIS_REST_URL/TOKEN (rate limits fall back to "
        "per-instance in-memory; build status falls back to the DB row)")
else:
    _redis_state = "in-memory"
print(f"[startup] storage={'supabase' if storage.using_supabase() else 'local'} "
      f"| redis={_redis_state} "
      f"| db={'on' if db.using_db() else 'off'}")

threading.Thread(target=_warmup, daemon=True).start()

# Clear any charts left over from a previous run on boot.
_sweep_charts()
