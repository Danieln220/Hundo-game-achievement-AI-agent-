"""FastAPI backend — exposes the SAME agent over HTTP for a React frontend.

The agent does not change; this just wraps run() / make_chart() / ensure_snapshot()
in endpoints. ALL logic stays in agent/ and data_layer/ — keep this file thin.

Run locally:
    uvicorn api.main:app --reload --port 8000
Then open http://localhost:8000/docs for the auto-generated API.
"""
import json
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import time

from config import CORS_ORIGINS, CHART_TTL_HOURS, CHART_MAX_FILES, missing_secrets
from agent import run, run_stream, make_chart
from data_layer import steam_client
from data_layer.resolver import resolve_steam_id, SteamResolveError
from data_layer.snapshot import ensure_snapshot, load_frames, PrivateProfileError

_CHARTS_DIR = Path(__file__).parent.parent / "data" / "charts"
_CHARTS_DIR.mkdir(parents=True, exist_ok=True)


def _sweep_charts() -> int:
    """Delete stale/excess chart PNGs so data/charts/ stays bounded.

    Charts are disposable (regenerable from the snapshot), so this is safe:
    drop anything older than CHART_TTL_HOURS, then cap the dir to the newest
    CHART_MAX_FILES. Best-effort — never raises into a request. Returns the
    number of files removed. Multi-user note: in a deploy this moves to object
    storage with a lifecycle/TTL policy (see CLAUDE.md "Multi-user / scaling
    architecture")."""
    try:
        files = sorted(
            _CHARTS_DIR.glob("*.png"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,  # newest first
        )
    except OSError:
        return 0

    now = time.time()
    ttl = CHART_TTL_HOURS * 3600
    removed = 0
    for i, p in enumerate(files):
        try:
            too_old = (now - p.stat().st_mtime) > ttl
            over_cap = i >= CHART_MAX_FILES
            if too_old or over_cap:
                p.unlink()
                removed += 1
        except OSError:
            pass
    return removed

# Fields that are large or internal — stripped from /ask responses.
_DROP_FIELDS = {"schema", "history", "with_insight"}

app = FastAPI(title="Hundo API", version="1.0", description="Steam achievement AI analyst")
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)
# Serve generated chart PNGs as static files (e.g. /charts/<hash>.png).
app.mount("/charts", StaticFiles(directory=str(_CHARTS_DIR)), name="charts")


def _chart_url(path: Optional[str]) -> Optional[str]:
    return f"/charts/{Path(path).name}" if path else None


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
def health():
    return {"status": "ok", "missing_secrets": missing_secrets()}


@app.post("/session")
def session(req: SessionReq):
    """Resolve a profile to a SteamID64, ensure its snapshot exists, and return a
    compact profile summary (avatar, persona, headline stats) for the UI header.
    Synchronous build (~15-30s on first load; longer for big libraries)."""
    _sweep_charts()  # opportunistic cleanup so long-running servers stay bounded
    try:
        steam_id = resolve_steam_id(req.profile)
    except SteamResolveError as e:
        raise HTTPException(status_code=404, detail=str(e))

    try:
        ensure_snapshot(steam_id)
    except PrivateProfileError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Couldn't load profile: {e}")

    frames = load_frames(steam_id)
    ach, pu, games = frames["achievements"], frames["player_unlocks"], frames["games"]

    # Headline stats for the profile header.
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


@app.post("/ask")
def ask(req: AskReq):
    """Answer a question. Returns the agent result (answer, route, trace fields,
    chart_url or chart_pending). For chart_pending answers, the client then calls
    /chart with this same result (answer-first UX)."""
    result = run(
        req.question,
        steam_id=req.steam_id,
        history=req.history,
        with_insight=req.with_insight,
    )
    return _serialize(result)


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


@app.post("/ask/stream")
def ask_stream(req: AskReq):
    """Streaming variant of /ask (Server-Sent Events). Emits `progress` events as
    each agent node fires, then a final `result` event with the serialized payload.
    The client reads this as a stream (fetch + ReadableStream)."""
    def gen():
        for kind, payload in run_stream(
            req.question,
            steam_id=req.steam_id,
            history=req.history,
            with_insight=req.with_insight,
        ):
            if kind == "progress":
                yield _sse("progress", {"node": payload})
            else:
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


import threading
threading.Thread(target=_warmup, daemon=True).start()

# Clear any charts left over from a previous run on boot.
_sweep_charts()
