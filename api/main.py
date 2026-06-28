"""FastAPI backend — exposes the SAME agent over HTTP for a React frontend.

The agent does not change; this just wraps run() / make_chart() / ensure_snapshot()
in endpoints. ALL logic stays in agent/ and data_layer/ — keep this file thin.

Run locally:
    uvicorn api.main:app --reload --port 8000
Then open http://localhost:8000/docs for the auto-generated API.
"""
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
from pydantic import BaseModel

from config import (
    CORS_ORIGINS, CHART_TTL_HOURS, CHART_MAX_FILES, missing_secrets,
    RATE_LIMIT_ASK_PER_MIN, RATE_LIMIT_ASK_PER_DAY, RATE_LIMIT_SESSION_PER_MIN,
    SNAPSHOT_TTL_DAYS, PUBLIC_API_URL, FRONTEND_URL,
)
from agent import run, run_stream, make_chart
from data_layer import steam_client
from data_layer import storage
from data_layer import cache
from data_layer import db
from data_layer.library import build_library

import time
from data_layer.resolver import resolve_steam_id, SteamResolveError
from data_layer.snapshot import (
    ensure_snapshot, has_snapshot, load_frames, clear_snapshot, PrivateProfileError,
)

import threading

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
    client is the first hop in X-Forwarded-For."""
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


def _serialize(result: dict) -> dict:
    """Strip heavy/internal fields and turn a local chart_path into a chart_url."""
    out = {k: v for k, v in result.items() if k not in _DROP_FIELDS}
    out["chart_url"] = _chart_url(out.pop("chart_path", None))
    return out


# ── Request models ────────────────────────────────────────────────────────────

class SessionReq(BaseModel):
    profile: str                       # alias, custom URL, profile link, or SteamID64


class AskReq(BaseModel):
    question: str
    steam_id: Optional[str] = None     # None → server's default STEAM_ID
    history: Optional[list[dict]] = None
    with_insight: bool = True


class ChartReq(BaseModel):
    result: dict                       # a prior /ask response (echoed back)


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.get("/health")
def health(request: Request, debug: int = 0):
    """Cheap liveness for the platform's frequent health checks. Pass ?debug=1 for
    a live cache round-trip + the client IP this host sees (diagnostics) — kept off
    by default so routine checks don't burn the Upstash command quota."""
    out = {"status": "ok", "missing_secrets": missing_secrets()}
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
        return RedirectResponse(f"{FRONTEND_URL}/?steam_id={steam_id}")
    return RedirectResponse(f"{FRONTEND_URL}/?login_error=1")


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


def _session_summary(steam_id: str) -> dict:
    """Headline profile stats for the UI header (assumes the snapshot is ready)."""
    frames = load_frames(steam_id)
    ach, pu, games = frames["achievements"], frames["player_unlocks"], frames["games"]
    total_per = ach.groupby("appid").size()
    unlocked_per = pu[pu["achieved"]].groupby("appid").size().reindex(total_per.index, fill_value=0)
    perfect = int(((unlocked_per == total_per) & (total_per > 0)).sum())
    summary = {}
    try:
        summary = steam_client.get_player_summary(steam_id)
    except Exception:
        pass
    return {
        "steam_id": steam_id,
        "persona": summary.get("personaname", ""),
        "avatar": summary.get("avatarfull", ""),
        "games": int(len(games)),
        "unlocked": int(pu["achieved"].sum()),
        "total": int(len(ach)),
        "perfect": perfect,
    }


def _run_build(steam_id: str) -> None:
    """Background worker: build the snapshot, writing progress to the cache. The
    build-lock inside ensure_snapshot dedupes concurrent builds for the same user."""
    cache.set(_status_key(steam_id), "building", _STATUS_TTL)
    cache.set(_progress_key(steam_id), "0/0", _STATUS_TTL)

    def cb(done: int, total: int) -> None:
        cache.set(_progress_key(steam_id), f"{done}/{total}", _STATUS_TTL)

    try:
        ensure_snapshot(steam_id, progress_cb=cb)
        cache.set(_status_key(steam_id), "ready", _STATUS_TTL)
        _record_snapshot_meta(steam_id)
    except PrivateProfileError as e:
        cache.set(_status_key(steam_id), f"failed:{e}", _STATUS_TTL)
        db.upsert_snapshot(steam_id, status="failed", error=str(e))
    except Exception as e:
        cache.set(_status_key(steam_id), f"failed:Couldn't load profile: {e}", _STATUS_TTL)
        db.upsert_snapshot(steam_id, status="failed", error=str(e))


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
    _sweep_charts()      # opportunistic cleanup so long-running servers stay bounded
    _sweep_snapshots()   # GC snapshots not rebuilt in SNAPSHOT_TTL_DAYS (DB-driven)
    try:
        steam_id = resolve_steam_id(req.profile)
    except SteamResolveError as e:
        raise HTTPException(status_code=404, detail=str(e))

    if has_snapshot(steam_id):
        cache.set(_status_key(steam_id), "ready", _STATUS_TTL)
        summ = _session_summary(steam_id)
        db.upsert_user(steam_id, req.profile, summ["persona"], summ["avatar"])
        return {"status": "ready", **summ}

    # Not built yet → record identity, launch a background build, tell client to poll.
    db.upsert_user(steam_id, req.profile)
    cache.set(_status_key(steam_id), "building", _STATUS_TTL)
    cache.set(_progress_key(steam_id), "0/0", _STATUS_TTL)
    threading.Thread(target=_run_build, args=(steam_id,), daemon=True).start()
    return {"status": "building", "steam_id": steam_id}


@app.get("/library")
def library(steam_id: str):
    """The full trophy-case dataset (profile, cards, games, curator highlights) for
    a built profile. Read-only transform over the snapshot — see data_layer/library."""
    if not has_snapshot(steam_id):
        raise HTTPException(status_code=404, detail="No snapshot yet — load the profile first.")
    lib = build_library(steam_id)
    try:
        s = steam_client.get_player_summary(steam_id)
        lib["profile"]["name"] = s.get("personaname", "") or steam_id
        lib["profile"]["avatar"] = s.get("avatarfull", "")
    except Exception:
        lib["profile"]["name"] = lib["profile"]["name"] or steam_id
    return lib


@app.get("/popular")
def popular(steam_id: str):
    """Currently most-played Steam games the user does NOT own — discovery picks for
    a fresh 100% (the unowned-roadmap path handles these). appdetails are cached
    per-app (shared across users) so this stays cheap."""
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
    if has_snapshot(steam_id):
        summ = _session_summary(steam_id)
        db.upsert_user(steam_id, persona=summ["persona"], avatar=summ["avatar"])
        return {"status": "ready", **summ}

    status = cache.get(_status_key(steam_id)) or ""
    if status.startswith("failed:"):
        return {"status": "failed", "error": status[len("failed:"):]}

    prog = cache.get(_progress_key(steam_id)) or "0/0"
    try:
        done_s, total_s = prog.split("/")
        done, total = int(done_s), int(total_s)
    except ValueError:
        done, total = 0, 0
    pct = int(done * 100 / total) if total else 0
    return {"status": "building", "progress": {"done": done, "total": total, "pct": pct}}


@app.post("/ask")
def ask(req: AskReq):
    """Answer a question. Returns the agent result (answer, route, trace fields,
    chart_url or chart_pending). For chart_pending answers, the client then calls
    /chart with this same result (answer-first UX)."""
    t0 = time.perf_counter()
    result = run(
        req.question,
        steam_id=req.steam_id,
        history=req.history,
        with_insight=req.with_insight,
    )
    db.log_query(req.steam_id, req.question, result.get("route"),
                 int((time.perf_counter() - t0) * 1000))
    return _serialize(result)


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


@app.post("/ask/stream")
def ask_stream(req: AskReq):
    """Streaming variant of /ask (Server-Sent Events). Emits `progress` events as
    each agent node fires, then a final `result` event with the serialized payload.
    The client reads this as a stream (fetch + ReadableStream)."""
    def gen():
        t0 = time.perf_counter()
        for kind, payload in run_stream(
            req.question,
            steam_id=req.steam_id,
            history=req.history,
            with_insight=req.with_insight,
        ):
            if kind == "progress":
                yield _sse("progress", {"node": payload})
            elif kind == "token":
                yield _sse("token", {"text": payload})
            else:
                db.log_query(req.steam_id, req.question, payload.get("route"),
                             int((time.perf_counter() - t0) * 1000))
                yield _sse("result", _serialize(payload))

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.post("/chart")
def chart(req: ChartReq):
    """Second-pass chart generation for a prior /ask result."""
    return {"chart_url": _chart_url(make_chart(req.result))}


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
print(f"[startup] storage={'supabase' if storage.using_supabase() else 'local'} "
      f"| redis={'upstash' if cache.using_redis() else 'in-memory'} "
      f"| db={'on' if db.using_db() else 'off'}")

threading.Thread(target=_warmup, daemon=True).start()

# Clear any charts left over from a previous run on boot.
_sweep_charts()
