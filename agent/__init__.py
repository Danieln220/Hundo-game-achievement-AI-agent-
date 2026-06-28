"""Public entry point for the agent — the seam every UI calls.

Both the Streamlit app (now) and the future FastAPI backend import this:

    from agent import run
    result = run("Which of my games am I closest to 100%?", steam_id="7656...")

Keep ALL UI code out of this package. If app.py stays thin and only calls
run(), the React + FastAPI upgrade is 'wrap run() in an endpoint', not a
rewrite."""
from typing import Optional

from config import STEAM_ID, DEEPSEEK_MODEL_FLASH
from data_layer.snapshot import load_frames
from .graph import build_graph, generate_chart
from .llm import call_llm

# The AI itself decides whether a message is small-talk vs a real data question —
# no hardcoded word lists. ONE cheap Flash call either returns a friendly reply, or
# the __DATA__ sentinel meaning "this needs the analysis pipeline."
_DATA_TOKEN = "__DATA__"
_TRIAGE_REPLY_SYSTEM = (
    "You are Hundo, a friendly Steam achievement analyst.\n"
    "If the user's message is a greeting, thanks, small-talk, or a question about you or your "
    "capabilities (NOT a question about their Steam games/achievements), reply warmly in ONE "
    "short sentence and invite them to ask about their achievements.\n"
    "If instead it IS a question about their Steam data, reply with EXACTLY this token and "
    f"nothing else: {_DATA_TOKEN}\n"
    "IMPORTANT: Anything that names or refers to a specific game, or asks about achievements, "
    "completion, rarity, what to play, how to unlock something, OR HOW LONG / how many hours / "
    "time to complete or 100% a game, is a DATA question — emit the token and do NOT answer it "
    "yourself from general knowledge. When unsure, emit the token."
)


def _smalltalk_reply(question: str) -> Optional[str]:
    """One cheap Flash call. Returns a greeting/small-talk reply, or None if the
    message is a real data question (the model emits the __DATA__ sentinel)."""
    try:
        resp = call_llm(question, model=DEEPSEEK_MODEL_FLASH, system=_TRIAGE_REPLY_SYSTEM).strip()
    except Exception:
        return None  # on any failure, fall through to the normal pipeline
    return None if _DATA_TOKEN in resp else resp


def run(
    question: str,
    steam_id: Optional[str] = None,
    history: Optional[list[dict]] = None,
    with_insight: bool = False,
) -> dict:
    """Load a user's cached Steam snapshot, build the graph, answer the question.

    `steam_id` selects whose data to analyze; None falls back to the configured
    default STEAM_ID. The snapshot must already exist — callers (the UI) are
    responsible for building it first via snapshot.ensure_snapshot().

    `history` is prior [{question, answer}] turns so follow-ups resolve.
    `with_insight` enables the proactive follow-up suggestion (UI on, eval off).

    Returns the final state dict (answer, chart_path, trace fields, ...)."""
    if not question or not question.strip():
        return {"answer": "Please enter a question.", "done": True}

    # Small-talk → one fast Flash reply, skip the analysis pipeline (AI decides).
    reply = _smalltalk_reply(question)
    if reply:
        return {"answer": reply, "done": True}

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
    return app.invoke({
        "question": question,
        "steam_id": steam_id,
        "history": history or [],
        "with_insight": with_insight,
        "retries": 0,
    })


def run_stream(
    question: str,
    steam_id: Optional[str] = None,
    history: Optional[list[dict]] = None,
    with_insight: bool = False,
):
    """Streaming variant of run() for live progress. Yields ("progress", node_name)
    as each graph node fires, then ("result", final_state) at the end. The fast
    paths (empty / greeting / no snapshot) yield a single ("result", ...)."""
    if not question or not question.strip():
        yield "result", {"answer": "Please enter a question.", "done": True}
        return

    reply = _smalltalk_reply(question)
    if reply:
        yield "result", {"answer": reply, "done": True}
        return

    steam_id = steam_id or STEAM_ID
    frames = load_frames(steam_id)
    if not frames or all(df.empty for df in frames.values()):
        yield "result", {"answer": "No snapshot data found for this profile.", "done": True}
        return

    app = build_graph(frames)

    # Run the graph in a background thread so answer TOKENS can stream out (via a
    # queue) WHILE a node is still running — app.stream alone only yields between
    # nodes. The graph pushes ("token", delta) through `_token_sink`; we also push
    # ("progress", node). The generator below drains the queue live.
    import queue as _queue
    import threading as _threading
    q: "_queue.Queue" = _queue.Queue()
    holder: dict = {}
    inp = {
        "question": question,
        "steam_id": steam_id,
        "history": history or [],
        "with_insight": with_insight,
        "retries": 0,
        "_token_sink": lambda tok: q.put(("token", tok)),
    }

    def _worker():
        try:
            for mode, chunk in app.stream(inp, stream_mode=["updates", "values"]):
                if mode == "updates":
                    for node_name in chunk:
                        q.put(("progress", node_name))
                else:
                    holder["final"] = chunk
        except Exception as exc:  # surface, don't hang the stream
            holder["final"] = {"answer": f"⚠️ {exc}", "done": True}
        finally:
            q.put(("__done__", None))

    _threading.Thread(target=_worker, daemon=True).start()
    while True:
        kind, payload = q.get()
        if kind == "__done__":
            break
        yield kind, payload
    yield "result", holder.get("final", {})


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
    state = dict(result)
    if not state.get("schema"):
        # The API drops the large schema from responses; rebuild it from frames
        # so a chart can be generated from just the lightweight result echo.
        from .graph import _build_schema
        state["schema"] = _build_schema(frames)
    return generate_chart(state, frames)
