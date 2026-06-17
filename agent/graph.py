"""LangGraph agent core — UI-agnostic.

Flow:
    question -> inspect_schema -> planner -> (route) ->
        analysis: write_code -> execute_code -> validate_output -> (reflect?)
                  -> [retry write_code | finalize]
        howto (stretch): howto_search -> finalize
    finalize -> END
"""
from __future__ import annotations

import operator
import re
import uuid
from pathlib import Path
from typing import Annotated, Literal, Optional, TypedDict

import pandas as pd
from langgraph.graph import START, END, StateGraph

from config import MAX_RETRIES, DEEPSEEK_MODEL_PRO, DEEPSEEK_MODEL_FLASH
from .sandbox import run_user_code
from .llm import call_llm
from .search import web_search


class AgentState(TypedDict, total=False):
    question: str
    steam_id: Optional[str]            # whose snapshot the sandbox loads
    schema: str                        # compact description of the DataFrames
    plan: str
    route: Literal["analysis", "howto"]
    last_code: str
    code_history: Annotated[list[str], operator.add]
    last_result: Optional[str]
    last_error: Optional[str]
    validation_error: Optional[str]
    retries: int
    sources: Annotated[list[dict], operator.add]
    answer: Optional[str]
    chart_pending: bool                # True if a chart applies (generated post-answer)
    chart_path: Optional[str]
    done: bool


# ── System prompts ────────────────────────────────────────────────────────────

_PLANNER_SYSTEM = """You are a data analyst for Steam achievement data.
You have three DataFrames:
- games:          appid, name, playtime (minutes)
- achievements:   appid, api_name, display_name, description, rarity_pct (% of ALL players who unlocked it), hidden (bool)
- player_unlocks: appid, api_name, achieved (bool), unlock_time (unix timestamp)

CRITICAL column meanings:
- achievements.hidden = True means the achievement DESCRIPTION is spoiler-hidden in Steam.
  It does NOT mean the player has or has not unlocked it.
  To check how many the PLAYER has unlocked, use player_unlocks.achieved == True.
- player_unlocks.achieved = True means THIS PLAYER has unlocked that achievement.

Given the schema and the user question, respond in this EXACT format:
PLAN:
- step 1
- step 2
- step 3
ROUTE: analysis

Use ROUTE: analysis for any data or stats question answerable with pandas.
Use ROUTE: howto ONLY if the user is asking HOW to unlock a specific achievement (tips/guide)."""

_CODE_SYSTEM = """You are a pandas expert writing code to analyze Steam achievement data.

Pre-loaded variables (do NOT import or redefine them):
- games          DataFrame columns: appid, name, playtime
- achievements   DataFrame columns: appid, api_name, display_name, description, rarity_pct, hidden
- player_unlocks DataFrame columns: appid, api_name, achieved, unlock_time
- pd  (pandas already imported)
- np  (numpy already imported)

CRITICAL column meanings:
- achievements.hidden  = whether the achievement DESCRIPTION is spoiler-hidden in Steam.
  This has NOTHING to do with whether the player has unlocked it.
  To count how many achievements have hidden descriptions: achievements['hidden'].sum()
- player_unlocks.achieved = whether THIS PLAYER has unlocked that achievement (True/False).
  To count what the player has unlocked: player_unlocks['achieved'].sum()
  To count hidden-description achievements the player has ALSO unlocked:
    achievements[achievements['hidden']].merge(player_unlocks[player_unlocks['achieved']], on=['appid','api_name'])

CRITICAL pattern — "games with progress" means games where the player has unlocked AT LEAST ONE achievement,
NOT games with playtime > 0. Always derive this from player_unlocks:
  games_with_progress_appids = player_unlocks[player_unlocks['achieved']]['appid'].unique()
Then filter your completion table to those appids BEFORE finding the minimum.

Common analysis patterns:
- "easiest achievements I haven't unlocked" / "easy wins": merge achievements with the player's
  unlocked rows, keep achievements NOT unlocked (achieved is False or missing), sort by rarity_pct
  DESCENDING (high % = most players have it = easiest), take the top N.
- "stuck" / "overlooked" achievements: in a game the player has played, achievements with HIGH
  rarity_pct (most players unlocked them) that THIS player has NOT unlocked — likely missed/stuck.
- "how many achievements away from completing X": total achievements in the game minus unlocked count.
- "what should I play next to complete": among games with progress but <100%, rank by FEWEST
  achievements remaining (closest to done).

Rules:
1. Assign your FINAL answer to a variable called `result`
2. `result` must be a string or convertible to string via str()
3. Do NOT use print() — only assign to `result`
4. Do NOT add any import statements
5. Keep it simple and correct

Output ONLY the raw Python code. No markdown fences, no explanation."""

_HOWTO_SYSTEM = """You are a Steam achievement guide assistant.
Given the user's question and a few web search snippets, write a practical,
concise how-to answer: the concrete steps or tips to unlock the achievement.
Base it on the snippets — if they're thin or conflicting, say what's known and
don't invent specifics. Keep it under ~6 sentences, then end with a 'Sources:'
list of the URLs you actually used."""

_FINALIZE_SYSTEM = """You are a helpful assistant summarizing Steam achievement analysis results.
Given the question and the computed result, write a clear, concise natural-language answer.
Be specific — include the actual numbers, game names, and percentages from the result.
Keep it to 2-3 sentences max. Do not mention code or DataFrames."""

_CHART_YES_PATTERNS = re.compile(
    r"\b(which game|top\s+\d|closest|most played|highest|lowest|best game|worst game"
    r"|across games?|per game|each game|breakdown|distribution|compare|ranking"
    r"|least completion|most achievement|rarest.{0,20}top|top.{0,20}rare)\b",
    re.IGNORECASE,
)

_CHART_CODE_SYSTEM = """You are a matplotlib expert writing code to visualize Steam achievement data.

Pre-loaded variables (do NOT redefine):
- games, achievements, player_unlocks (DataFrames)
- pd, np, plt (matplotlib.pyplot)
- CHART_PATH (string — the file path to save the figure to)

CRITICAL — stay consistent with the analysis:
You are given the ANALYSIS CODE that produced the answer. REUSE its exact filtering,
joins, and sorting so the chart shows the SAME items as the answer. Do NOT re-derive
the data with different logic. Your only change is to show the top N rows (≈10) instead
of just the single top result, then plot the same metric the analysis computed.

Avoid empty bars:
- Plot the metric the analysis used (e.g. completion %, or rarity_pct for "rarest").
- DROP rows where that metric is NaN/missing before plotting (e.g. dropna on it).
- If the analysis only considered UNLOCKED achievements, keep that same filter.

Rules:
1. Create one clear, well-labeled chart (title, axis labels, readable font sizes)
2. For long names use a horizontal bar chart (barh) with tight layout
3. Save with: plt.savefig(CHART_PATH, bbox_inches='tight', dpi=120)
4. Call plt.close() after saving
5. Set result = CHART_PATH
6. Output ONLY raw Python code, no markdown fences"""

_CHARTS_DIR = Path(__file__).parent.parent / "data" / "charts"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _extract_code(text: str) -> str:
    """Strip markdown code fences if the LLM wrapped its response."""
    match = re.search(r"```(?:python)?\n(.*?)```", text, re.DOTALL)
    if match:
        return match.group(1).strip()
    return text.strip()


_COLUMN_NOTES: dict[str, dict[str, str]] = {
    "games": {
        "appid":    "Steam application ID (join key)",
        "name":     "game title",
        "playtime": "total minutes played",
    },
    "achievements": {
        "appid":        "join key -> games",
        "api_name":     "internal achievement identifier (join key -> player_unlocks)",
        "display_name": "human-readable achievement name shown in Steam",
        "description":  "text hint shown to players (empty string when hidden=True)",
        "rarity_pct":   "float: % of ALL Steam players who have unlocked this achievement",
        "hidden":       "bool: True means the achievement DESCRIPTION is secret (spoiler-hidden) "
                        "-- it does NOT mean the player has unlocked it. "
                        "To check whether a player has unlocked an achievement use player_unlocks.achieved.",
    },
    "player_unlocks": {
        "appid":       "join key -> games",
        "api_name":    "join key -> achievements",
        "achieved":    "bool: True means THIS PLAYER has unlocked the achievement",
        "unlock_time": "unix timestamp of unlock (0 if not yet unlocked)",
    },
}


def _build_schema(frames: dict[str, pd.DataFrame]) -> str:
    lines = []
    for name, df in frames.items():
        lines.append(f"DataFrame '{name}'  shape={df.shape}")
        notes = _COLUMN_NOTES.get(name, {})
        col_info = []
        for col in df.columns:
            note = notes.get(col, "")
            dtype = str(df[col].dtype)
            col_info.append(f"    {col} ({dtype}){': ' + note if note else ''}")
        lines.append("  columns:")
        lines.extend(col_info)
        if not df.empty:
            lines.append(f"  sample:\n{df.head(2).to_string(index=False)}")
    return "\n".join(lines)


def generate_chart(state: AgentState, frames: dict[str, pd.DataFrame]) -> Optional[str]:
    """Keyword-check if a chart helps, then ask Pro to write the code, run it in the sandbox.
    Returns the saved chart path, or None if not applicable or generation failed."""
    if not _CHART_YES_PATTERNS.search(state.get("question", "")):
        return None

    _CHARTS_DIR.mkdir(parents=True, exist_ok=True)
    chart_path = str(_CHARTS_DIR / f"{uuid.uuid4().hex}.png")

    prompt = (
        f"Schema:\n{state['schema']}\n\n"
        f"Question: {state['question']}\n\n"
        f"ANALYSIS CODE (reuse this exact logic, just show the top ~10):\n"
        f"{state.get('last_code', '')}\n\n"
        f"Analysis result (the answer shown to the user):\n{state.get('last_result', '')}\n\n"
        f"CHART_PATH = {chart_path!r}\n\n"
        "Write matplotlib code to visualize this result consistently with the analysis. "
        "Use CHART_PATH to save the figure and set result = CHART_PATH."
    )
    raw = call_llm(prompt, model=DEEPSEEK_MODEL_PRO, system=_CHART_CODE_SYSTEM)
    code = f"CHART_PATH = {chart_path!r}\n{_extract_code(raw)}"

    _, error = run_user_code(code, frames, state.get("steam_id"))
    if error or not Path(chart_path).exists():
        return None
    return chart_path


# ── Nodes ─────────────────────────────────────────────────────────────────────

def inspect_schema_node(state: AgentState, frames: dict[str, pd.DataFrame]) -> AgentState:
    """Summarize columns + sample rows so the LLM never invents column names."""
    return {"schema": _build_schema(frames)}


def planner_node(state: AgentState) -> AgentState:
    """FLASH model: given question + schema, return a plan and route."""
    prompt = f"Schema:\n{state['schema']}\n\nQuestion: {state['question']}"
    response = call_llm(prompt, model=DEEPSEEK_MODEL_FLASH, system=_PLANNER_SYSTEM)

    plan_lines, route, in_plan = [], "analysis", False
    for line in response.splitlines():
        if line.startswith("PLAN:"):
            in_plan = True
        elif line.startswith("ROUTE:"):
            in_plan = False
            route = "howto" if "howto" in line.lower() else "analysis"
        elif in_plan and line.strip():
            plan_lines.append(line.strip())

    return {"plan": "\n".join(plan_lines), "route": route, "retries": 0}


def write_code_node(state: AgentState) -> AgentState:
    """PRO model: write pandas code. On retry, feed back the error so it can fix it."""
    error_context = ""
    if state.get("last_error"):
        error_context += f"\nPrevious attempt FAILED with this error — fix it:\n{state['last_error']}\n"
    if state.get("validation_error"):
        error_context += f"\nPrevious result was INVALID — produce a valid one:\n{state['validation_error']}\n"

    prompt = (
        f"Schema:\n{state['schema']}\n\n"
        f"Plan:\n{state['plan']}\n"
        f"{error_context}\n"
        f"Question: {state['question']}\n\n"
        "Write pandas code. Assign the final answer to `result`."
    )
    raw = call_llm(prompt, model=DEEPSEEK_MODEL_PRO, system=_CODE_SYSTEM)
    code = _extract_code(raw)
    return {"last_code": code, "code_history": [code]}


def execute_code_node(state: AgentState, frames: dict[str, pd.DataFrame]) -> AgentState:
    """Run code in the sandbox; always increment retries to bound the loop."""
    result, error = run_user_code(state["last_code"], frames, state.get("steam_id"))
    return {
        "last_result": result,
        "last_error": error,
        "validation_error": None,
        "retries": state.get("retries", 0) + 1,
    }


def validate_output_node(state: AgentState) -> AgentState:
    """Rule-based plausibility check. Sets validation_error to force a retry."""
    if state.get("last_error"):
        return {"validation_error": None}  # error already captured upstream

    result = (state.get("last_result") or "").strip()

    if not result:
        return {"validation_error": "Result is empty."}

    # Only validate range when result explicitly contains a % sign
    pct_match = re.fullmatch(r"(\d+\.?\d*)\s*%", result)
    if pct_match:
        val = float(pct_match.group(1))
        if not 0 <= val <= 100:
            return {"validation_error": f"Percentage {val} is outside [0, 100]."}

    # Negative counts are implausible
    if re.fullmatch(r"-\d+", result):
        return {"validation_error": f"Count cannot be negative: {result}"}

    return {"validation_error": None}


def howto_search_node(state: AgentState) -> AgentState:
    """Web-search for how to unlock a specific achievement, then synthesize a
    practical answer with sources. Produces the FINAL answer (routes to END),
    so it must not depend on the analysis pipeline's last_result."""
    question = state["question"]
    results = web_search(f"{question} Steam achievement guide", max_results=3)

    if not results:
        return {
            "answer": (
                "I couldn't find a guide for that — web search is unavailable or "
                "returned nothing. Try the in-game Steam community guides for that title."
            ),
            "sources": [],
            "done": True,
        }

    context = "\n\n".join(
        f"[{i}] {r['title']}\n{r['url']}\n{r['content']}"
        for i, r in enumerate(results, 1)
    )
    prompt = (
        f"Question: {question}\n\n"
        f"Web search results:\n{context}\n\n"
        "Write a practical how-to answer with concrete steps, then list the source URLs."
    )
    answer = call_llm(prompt, model=DEEPSEEK_MODEL_PRO, system=_HOWTO_SYSTEM)
    return {"answer": answer, "sources": results, "done": True}


def finalize_node(state: AgentState, frames: dict[str, pd.DataFrame]) -> AgentState:
    """FLASH model: summarize the computed result into a natural-language answer.
    Abstains honestly if retries ran out with no result.

    The chart is NOT generated here — it's an extra Pro call + render that would
    block the answer. The UI shows this answer first, then calls agent.make_chart()
    for an answer-first experience. We only flag whether a chart applies."""
    if not state.get("last_result"):
        answer = (
            f"I couldn't compute a reliable answer after {state.get('retries', 0)} attempt(s). "
            f"Last error: {state.get('last_error') or 'unknown'}"
        )
        return {"answer": answer, "done": True}

    chart_pending = bool(_CHART_YES_PATTERNS.search(state.get("question", "")))

    prompt = (
        f"Question: {state['question']}\n\n"
        f"Computed result:\n{state['last_result']}\n\n"
        "Write a clear, concise natural-language answer."
    )
    answer = call_llm(prompt, model=DEEPSEEK_MODEL_FLASH, system=_FINALIZE_SYSTEM)
    return {"answer": answer, "chart_pending": chart_pending, "done": True}


# ── Routing ───────────────────────────────────────────────────────────────────

def route_after_planner(state: AgentState) -> Literal["write_code", "howto_search"]:
    return "write_code" if state.get("route") == "analysis" else "howto_search"


def route_after_validate(state: AgentState) -> Literal["write_code", "finalize"]:
    """Retry if the code crashed OR produced an implausible result, within budget."""
    has_problem = state.get("last_error") or state.get("validation_error")
    if has_problem and state.get("retries", 0) < MAX_RETRIES:
        return "write_code"
    return "finalize"


# ── Graph ─────────────────────────────────────────────────────────────────────

def build_graph(frames: dict[str, pd.DataFrame]):
    g = StateGraph(AgentState)
    g.add_node("inspect_schema", lambda s: inspect_schema_node(s, frames))
    g.add_node("planner",        planner_node)
    g.add_node("write_code",     write_code_node)
    g.add_node("execute_code",   lambda s: execute_code_node(s, frames))
    g.add_node("validate_output", validate_output_node)
    g.add_node("howto_search",   howto_search_node)
    g.add_node("finalize",       lambda s: finalize_node(s, frames))

    g.add_edge(START, "inspect_schema")
    g.add_edge("inspect_schema", "planner")
    g.add_conditional_edges("planner", route_after_planner)
    g.add_edge("write_code", "execute_code")
    g.add_edge("execute_code", "validate_output")
    g.add_conditional_edges("validate_output", route_after_validate)
    g.add_edge("howto_search", END)   # how-to node yields the final answer itself
    g.add_edge("finalize", END)
    return g.compile()
