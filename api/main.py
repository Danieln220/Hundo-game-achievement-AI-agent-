"""FastAPI backend — exposes the SAME agent over HTTP for a React frontend.

The agent does not change; this just wraps run() / make_chart() / ensure_snapshot()
in endpoints. ALL logic stays in agent/ and data_layer/ — keep this file thin.

Run locally:
    uvicorn api.main:app --reload --port 8000
Then open http://localhost:8000/docs for the auto-generated API.
"""
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from config import CORS_ORIGINS, missing_secrets
from agent import run, make_chart
from data_layer.resolver import resolve_steam_id, SteamResolveError
from data_layer.snapshot import ensure_snapshot, load_frames, PrivateProfileError

_CHARTS_DIR = Path(__file__).parent.parent / "data" / "charts"
_CHARTS_DIR.mkdir(parents=True, exist_ok=True)

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
    """Resolve a profile to a SteamID64 and ensure its snapshot exists.
    Synchronous build (~15-30s on first load; longer for big libraries)."""
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
    return {"steam_id": steam_id, "games": int(len(frames["games"]))}


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


@app.post("/chart")
def chart(req: ChartReq):
    """Second-pass chart generation for a prior /ask result."""
    return {"chart_url": _chart_url(make_chart(req.result))}
