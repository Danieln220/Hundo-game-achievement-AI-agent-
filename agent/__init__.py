"""Public entry point for the agent — the seam every UI calls.

Both the Streamlit app (now) and the future FastAPI backend import this:

    from agent import run
    result = run("Which of my games am I closest to 100%?", steam_id="7656...")

Keep ALL UI code out of this package. If app.py stays thin and only calls
run(), the React + FastAPI upgrade is 'wrap run() in an endpoint', not a
rewrite."""
import re
from typing import Optional

from config import STEAM_ID, DEEPSEEK_MODEL_FLASH
from data_layer.snapshot import load_frames
from .graph import build_graph, generate_chart
from .llm import call_llm

# Greetings / small-talk skip the analysis pipeline and get ONE fast Flash reply
# (the LLM still answers — it just doesn't pay for a full plan+code+verify round-trip).
_GREETINGS = {
    "hi", "hello", "hey", "yo", "hiya", "howdy", "sup", "hii", "helloo", "hello there",
    "good morning", "good afternoon", "good evening", "gm",
    "thanks", "thank you", "ty", "thx", "cheers", "thank u",
    "how are you", "hows it going", "whats up", "wassup",
    "what can you do", "what do you do", "who are you", "what are you", "help",
}
_CHITCHAT_SYSTEM = (
    "You are Hundo, a friendly Steam achievement analyst. The user sent a greeting or "
    "small-talk. Reply warmly in ONE short sentence and invite them to ask about their "
    "Steam achievements (e.g. games closest to 100%, rarest achievements, how to unlock a "
    "specific achievement). Do not invent any stats."
)


def _is_greeting(text: str) -> bool:
    norm = re.sub(r"[^a-z' ]", "", text.lower())
    norm = re.sub(r"\s+", " ", norm).strip()
    return norm in _GREETINGS


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

    # Greeting / small-talk → one fast Flash reply, skip the analysis pipeline.
    if _is_greeting(question):
        return {"answer": call_llm(question, model=DEEPSEEK_MODEL_FLASH, system=_CHITCHAT_SYSTEM),
                "done": True}

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
