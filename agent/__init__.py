"""Public entry point for the agent — the seam every UI calls.

Both the Streamlit app (now) and the future FastAPI backend import this:

    from agent import run
    result = run("Which of my games am I closest to 100%?", steam_id="7656...")

Keep ALL UI code out of this package. If app.py stays thin and only calls
run(), the React + FastAPI upgrade is 'wrap run() in an endpoint', not a
rewrite."""
from typing import Optional

from config import STEAM_ID
from data_layer.snapshot import load_frames
from .graph import build_graph, generate_chart


def run(question: str, steam_id: Optional[str] = None) -> dict:
    """Load a user's cached Steam snapshot, build the graph, answer the question.

    `steam_id` selects whose data to analyze; None falls back to the configured
    default STEAM_ID. The snapshot must already exist — callers (the UI) are
    responsible for building it first via snapshot.ensure_snapshot().

    Returns the final state dict (answer, chart_path, trace fields, ...)."""
    if not question or not question.strip():
        return {"answer": "Please enter a question.", "done": True}

    steam_id = steam_id or STEAM_ID
    frames = load_frames(steam_id)

    if not frames or all(df.empty for df in frames.values()):
        return {
            "answer": (
                "No snapshot data found for this profile. Build it first "
                "(the app does this automatically when you enter a Steam ID)."
            ),
            "done": True,
        }

    app = build_graph(frames)
    return app.invoke({"question": question, "steam_id": steam_id})


def make_chart(result: dict) -> Optional[str]:
    """Generate a chart for an already-computed result (answer-first UX).

    The UI calls run() first, shows the answer, then calls this only when
    result['chart_pending'] is True. Returns a chart file path or None.
    Kept as a separate seam so the chart's extra Pro call + render never
    blocks the answer the user is waiting for."""
    if not result or not result.get("last_result"):
        return None
    steam_id = result.get("steam_id") or STEAM_ID
    frames = load_frames(steam_id)
    return generate_chart(result, frames)
