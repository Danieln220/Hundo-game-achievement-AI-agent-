"""LangGraph agent core — UI-agnostic.

Flow:
    question -> inspect_schema -> planner -> (route) ->
        analysis: write_code -> execute_code -> validate_output -> (reflect?)
                  -> [retry write_code | finalize]
        howto (stretch): howto_search -> finalize
    finalize -> END
"""
from __future__ import annotations

import json
import operator
import re
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import date
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
    history: list[dict]                # prior [{question, answer}] turns for follow-ups
    with_insight: bool                 # generate a proactive nudge (UI on, eval off)
    schema: str                        # compact description of the DataFrames
    plan: str
    route: Literal["analysis", "howto", "clarify", "chitchat", "roadmap"]
    interpretation: Optional[str]      # how the agent read an ambiguous question
    clarify_question: Optional[str]    # question to ask back when route == clarify
    last_code: str
    code_history: Annotated[list[str], operator.add]
    last_result: Optional[str]
    last_error: Optional[str]
    validation_error: Optional[str]
    verification_error: Optional[str]  # set by the self-verification node
    retries: int
    sources: Annotated[list[dict], operator.add]
    answer: Optional[str]
    insight: Optional[str]             # proactive follow-up suggestion
    chart_pending: bool                # True if a chart applies (generated post-answer)
    chart_path: Optional[str]
    done: bool


# ── System prompts ────────────────────────────────────────────────────────────

_PLAN_CODE_SYSTEM = """You are a data analyst + pandas expert for Steam achievement data.
You both DECIDE how to handle the question and (for data questions) WRITE the pandas code,
in a single response — so be precise.

Pre-loaded in the sandbox (do NOT import or redefine):
- games          columns: appid, name, playtime (minutes)
- achievements   columns: appid, api_name, display_name, description, rarity_pct (% of ALL players), hidden (bool)
- player_unlocks columns: appid, api_name, achieved (bool), unlock_time (unix seconds)
- pd (pandas), np (numpy) already imported

CRITICAL column meanings:
- achievements.hidden = whether the achievement DESCRIPTION is spoiler-hidden. NOTHING to do with
  whether the player unlocked it. Count hidden-description achievements: achievements['hidden'].sum()
- player_unlocks.achieved = whether THIS PLAYER unlocked it. Count unlocked: player_unlocks['achieved'].sum()
  Hidden-description achievements the player ALSO unlocked:
    achievements[achievements['hidden']].merge(player_unlocks[player_unlocks['achieved']], on=['appid','api_name'])

CRITICAL pattern — "games with progress" = games with AT LEAST ONE achievement unlocked, NOT playtime>0:
  games_with_progress_appids = player_unlocks[player_unlocks['achieved']]['appid'].unique()

Common analysis patterns:
- "closest to 100%" / "almost done": rank by completion %, but EXCLUDE games already at 100%.
- "easiest achievements I haven't unlocked" / "easy wins": achievements NOT unlocked, sort rarity_pct DESC.
- "stuck"/"overlooked": played game, HIGH rarity_pct achievements the player has NOT unlocked.
- "how many away from completing X": total achievements in the game minus unlocked count.
- "what to play next": among games with progress but <100%, fewest achievements remaining.

Dates: unlock_time is Unix SECONDS. When a date is part of the answer, convert it with
pd.to_datetime(value, unit='s').strftime('%Y-%m-%d') — this needs NO import (pd is preloaded).
Do NOT write `import time` or `import datetime`. NEVER return a raw Unix integer.

Respond in this EXACT format:
ROUTE: analysis | howto | clarify | chitchat | roadmap
INTERPRETATION: <one line on how you read ambiguous wording, or NONE>
CLARIFY: <a single clarifying question, or NONE>
PLAN:
- step 1
- step 2
CODE:
<raw python ONLY when ROUTE is analysis; assign the final answer to `result`. Leave empty otherwise.>

Routing:
- analysis: any data/stats question answerable with pandas.
- howto: ONLY if the user asks HOW to unlock a specific achievement (tips/guide).
- clarify: ONLY if too ambiguous to answer sensibly even with a reasonable default AND the
  conversation gives no hint. Put your question in CLARIFY.
- chitchat: greetings, thanks, small-talk, or questions about you/your capabilities — anything
  NOT about the user's Steam data. (Leave CODE empty.)
- roadmap: the user wants a PLAN/ROADMAP to complete a game or reach a goal ("plan to 100%
  Rocket League", "path to my next 50 achievements", "what should I grind to finish X"). ALSO use
  roadmap when the user is REFINING a roadmap from earlier in the conversation ("only the easy
  ones", "skip multiplayer", "what about <other game>"). (Leave CODE empty — a dedicated step builds it.)

Ambiguity — prefer a sensible default over clarifying. If mildly ambiguous, use ROUTE: analysis,
state your reading in INTERPRETATION, and write code that follows it. Resolve follow-ups
("what about Rocket League?") from the conversation history.

Code rules (ROUTE: analysis): assign the final answer to `result` (string or str()-able);
no print(); no import statements; keep it simple and correct."""

_CHITCHAT_SYSTEM = """You are Hundo, a friendly Steam achievement analyst.
The user's message is small-talk, a greeting, or a question about you — not about their data.
Reply warmly in ONE short sentence and invite them to ask about their Steam achievements
(e.g. games closest to 100%, rarest achievements, how to unlock a specific achievement).
Do not invent any stats."""

_ROADMAP_CODE_SYSTEM = """You are a pandas expert building a completion-roadmap dataset.

Pre-loaded (do NOT redefine, do NOT import): games, achievements, player_unlocks, pd, np, json.

CRITICAL column meanings:
- player_unlocks.achieved = whether THIS PLAYER unlocked the achievement.
- achievements.rarity_pct = % of ALL players who unlocked it (float, may be NaN).
- achievements.hidden = whether the DESCRIPTION is spoiler-hidden (not about unlocking).

Goal: for the TARGET the user asks about (a specific game matched case-insensitively by name,
or — if no game is named — the whole library), list the achievements the player has NOT unlocked.

Steps:
- A locked achievement exists in `achievements` but has no player_unlocks row with achieved=True
  for that (appid, api_name).
- Sort locked achievements by rarity_pct DESCENDING (easiest first); NaN rarity goes last.
- Constraints: "skip multiplayer" / a named theme → exclude matching achievements. But "only the
  easy ones" / "easiest" just means return the easiest LOCKED ones (already sorted easiest-first) —
  do NOT return an empty list just because none exceed some rarity threshold.

Return EXACTLY (assign to `result`):
result = json.dumps({
  "target":   "<game name, or 'your library'>",
  "total":    <int total achievements for the target>,
  "unlocked": <int the player has unlocked for the target>,
  "remaining":[ {"name": <display_name>, "rarity_pct": <float or null>,
                 "description": <str>, "hidden": <bool>}, ... ]   # at most 40, easiest first
})

Output ONLY raw python, no markdown fences."""

_HOWTO_SYSTEM = """You are a Steam achievement guide assistant.
Given the user's question and a few web search snippets, write a practical,
concise how-to answer: the concrete steps or tips to unlock the achievement.
Base it on the snippets — if they're thin or conflicting, say what's known and
don't invent specifics. Keep it under ~6 sentences, then end with a 'Sources:'
list of the URLs you actually used."""

_FINALIZE_SYSTEM = """You are a helpful assistant summarizing Steam achievement analysis results.
Given the question and the computed result, write a clear, concise natural-language answer.
Be specific — include the actual numbers, game names, and percentages from the result.
Keep it to 2-3 sentences max. Do not mention code or DataFrames.
If an INTERPRETATION note is given, briefly note how you read the question (e.g. "Assuming you mean
games you haven't finished yet, ...")."""

_VERIFY_SYSTEM = """You are a strict reviewer checking whether a computed result actually answers
the user's question. You are given the question, the pandas code that ran, and its result.

Respond in this EXACT format:
VERDICT: OK  or  VERDICT: RETRY
REASON: <one line>

Say VERDICT: RETRY ONLY for a clear problem: the result doesn't address what was asked, the code
used obviously wrong logic, or the value is implausible. If it reasonably answers the question, say
VERDICT: OK. Do NOT nitpick formatting or phrasing — only correctness."""

_INSIGHT_SYSTEM = """You are a helpful Steam achievement coach. Given the user's question and the
answer they just received, add ONE short, genuinely useful follow-up suggestion or observation
(max 1 sentence). It must be relevant and specific. If nothing useful comes to mind, reply exactly
with NONE."""

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


def _format_history(state: AgentState, limit: int = 4) -> str:
    """Render the last few Q&A turns so the LLM can resolve follow-ups.
    Returns '' when there's no prior context (e.g. the eval's single-shot runs)."""
    history = state.get("history") or []
    if not history:
        return ""
    recent = history[-limit:]
    lines = []
    for turn in recent:
        q = turn.get("question", "")
        a = turn.get("answer", "")
        if q:
            lines.append(f"User: {q}")
        if a:
            lines.append(f"Assistant: {a}")
    return "Conversation so far:\n" + "\n".join(lines) + "\n\n" if lines else ""


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


def plan_code_node(state: AgentState) -> AgentState:
    """PRO model: in ONE call, decide the route + interpretation AND (for analysis)
    write the pandas code. Merging the old planner + write_code saves a whole LLM
    round-trip per question. On a retry, feed back the agent's OWN previous code +
    the error/critique so it fixes the specific mistake instead of re-deriving blind."""
    error_context = ""
    problem = state.get("last_error") or state.get("validation_error") or state.get("verification_error")
    if problem and state.get("last_code"):
        error_context += (
            "\nThis is a RETRY. Your PREVIOUS code (below) was not accepted — keep ROUTE: analysis "
            "and fix the specific issue:\n"
            f"```python\n{state['last_code']}\n```\n"
        )
    if state.get("last_error"):
        error_context += f"It FAILED with this error:\n{state['last_error']}\n"
    if state.get("validation_error"):
        error_context += f"Its result was INVALID: {state['validation_error']}\n"
    if state.get("verification_error"):
        error_context += f"A reviewer rejected its result: {state['verification_error']}\n"

    prompt = (
        f"Today's date is {date.today().isoformat()}. Unlock dates on or before today are valid "
        "and recent — never treat a date in 2026 as a 'future date' or data error.\n\n"
        f"Schema:\n{state['schema']}\n\n"
        f"{_format_history(state)}"
        f"{error_context}\n"
        f"Question: {state['question']}"
    )
    raw = call_llm(prompt, model=DEEPSEEK_MODEL_PRO, system=_PLAN_CODE_SYSTEM)

    # Parse the structured response.
    route, interpretation, clarify = "analysis", "", ""
    plan_lines, code_lines, section = [], [], None
    for line in raw.splitlines():
        if line.startswith("ROUTE:"):
            section = None
            low = line.lower()
            if "howto" in low:
                route = "howto"
            elif "clarify" in low:
                route = "clarify"
            elif "chitchat" in low:
                route = "chitchat"
            elif "roadmap" in low:
                route = "roadmap"
            else:
                route = "analysis"
        elif line.startswith("INTERPRETATION:"):
            section = None
            val = line.split(":", 1)[1].strip()
            interpretation = "" if val.upper() == "NONE" else val
        elif line.startswith("CLARIFY:"):
            section = None
            val = line.split(":", 1)[1].strip()
            clarify = "" if val.upper() == "NONE" else val
        elif line.startswith("PLAN:"):
            section = "plan"
        elif line.startswith("CODE:"):
            section = "code"
        elif section == "plan" and line.strip():
            plan_lines.append(line.strip())
        elif section == "code":
            code_lines.append(line)

    if route == "clarify" and not clarify:
        route = "analysis"

    out: AgentState = {
        "plan": "\n".join(plan_lines),
        "route": route,
        "interpretation": interpretation,
        "clarify_question": clarify,
    }
    if route == "analysis":
        code = _extract_code("\n".join(code_lines))
        out["last_code"] = code
        out["code_history"] = [code]
    return out


def execute_code_node(state: AgentState, frames: dict[str, pd.DataFrame]) -> AgentState:
    """Run code in the sandbox; always increment retries to bound the loop."""
    result, error = run_user_code(state["last_code"], frames, state.get("steam_id"))
    return {
        "last_result": result,
        "last_error": error,
        "validation_error": None,
        "verification_error": None,
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


def clarify_node(state: AgentState) -> AgentState:
    """Ambiguity too high to default — ask the user one clarifying question and
    end the turn. The UI keeps history, so the user's reply resolves it next turn."""
    question = state.get("clarify_question") or "Could you clarify what you mean?"
    return {"answer": question, "done": True}


def chitchat_node(state: AgentState) -> AgentState:
    """Greeting / small-talk that the regex fast-path didn't catch — answer with a
    single friendly Flash reply instead of running the analysis pipeline."""
    answer = call_llm(state["question"], model=DEEPSEEK_MODEL_FLASH, system=_CHITCHAT_SYSTEM)
    return {"answer": answer, "done": True}


def _run_verify(state: AgentState) -> Optional[str]:
    """FLASH critique: does the result actually answer the question?
    Returns a reason string when it should retry, else None. Called concurrently
    with finalize (they both only depend on the computed result), so verification
    adds ~0s on the happy path instead of a full extra round-trip."""
    prompt = (
        f"Question: {state['question']}\n\n"
        f"Code that ran:\n{state.get('last_code', '')}\n\n"
        f"Result: {state['last_result']}"
    )
    resp = call_llm(prompt, model=DEEPSEEK_MODEL_FLASH, system=_VERIFY_SYSTEM)

    retry, reason = False, ""
    for line in resp.splitlines():
        up = line.upper()
        if up.startswith("VERDICT:"):
            retry = "RETRY" in up
        elif up.startswith("REASON:"):
            reason = line.split(":", 1)[1].strip()
    return (reason or "Result did not pass review.") if retry else None


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


# ── Roadmap (flagship) ────────────────────────────────────────────────────────

_TIER_QUICK_MIN    = 50.0   # rarity_pct >= 50 → quick win (most players have it)
_TIER_MODERATE_MIN = 15.0   # 15–50 → moderate; < 15 (or unknown) → challenge/grind
_ROADMAP_MAX_HOWTO = 2      # bound web lookups to the hardest few
_ROADMAP_TIER_LIMIT = 10    # show at most N per tier


def _rarity(a: dict) -> Optional[float]:
    """Parse rarity_pct, mapping missing/NaN to None."""
    try:
        r = float(a.get("rarity_pct"))
        return None if r != r else r  # NaN != NaN
    except (TypeError, ValueError):
        return None


def roadmap_node(state: AgentState, frames: dict[str, pd.DataFrame]) -> AgentState:
    """Flagship: build a VERIFIED, tiered completion roadmap. The achievement data
    comes from the sandbox (real, still-locked achievements with real rarity), so it
    cannot be hallucinated. Ends with a refine invitation (human-in-the-loop)."""
    prompt = (
        f"Schema:\n{state['schema']}\n\n"
        f"{_format_history(state)}"
        f"Question: {state['question']}"
    )
    raw = call_llm(prompt, model=DEEPSEEK_MODEL_PRO, system=_ROADMAP_CODE_SYSTEM)
    code = _extract_code(raw)
    result, error = run_user_code(code, frames, state.get("steam_id"))

    data = None
    if result and not error:
        try:
            data = json.loads(result)
        except Exception:
            data = None
    if not data:
        return {
            "answer": ("I couldn't build a roadmap for that — try naming a specific game, "
                       "e.g. \"build me a plan to 100% Rocket League\"."),
            "code_history": [code],
            "done": True,
        }

    target   = data.get("target", "your library")
    total    = data.get("total", 0)
    unlocked = data.get("unlocked", 0)
    remaining = data.get("remaining") or []

    if not remaining:
        if total and unlocked >= total:
            answer = f"🎉 You've already unlocked every achievement for **{target}**!"
        else:
            answer = (
                f"No remaining achievements matched that filter for **{target}** "
                f"({unlocked}/{total} unlocked). Try widening it — e.g. drop the filter or "
                "include moderate-difficulty ones."
            )
        return {"answer": answer, "code_history": [code], "done": True}

    # Tier the remaining achievements by global rarity.
    quick, moderate, challenge = [], [], []
    for a in remaining:
        r = _rarity(a)
        if r is None or r < _TIER_MODERATE_MIN:
            challenge.append(a)
        elif r >= _TIER_QUICK_MIN:
            quick.append(a)
        else:
            moderate.append(a)

    # Bounded how-to lookups for the hardest named (non-hidden) achievements.
    howto_links = []
    hardest = sorted(
        [a for a in remaining if _rarity(a) is not None and not a.get("hidden")],
        key=_rarity,
    )[:_ROADMAP_MAX_HOWTO]
    for a in hardest:
        hits = web_search(f"{a['name']} {target} how to unlock", max_results=1)
        if hits:
            howto_links.append((a["name"], hits[0]["url"]))

    def _fmt(items: list) -> str:
        lines = []
        for a in items[:_ROADMAP_TIER_LIMIT]:
            r = _rarity(a)
            pct = f"{r:.0f}% have it" if r is not None else "rarity unknown"
            desc = "" if a.get("hidden") else (a.get("description") or "").strip()
            desc = f" — {desc}" if desc else ""
            lines.append(f"- **{a['name']}** ({pct}){desc}")
        if len(items) > _ROADMAP_TIER_LIMIT:
            lines.append(f"- …and {len(items) - _ROADMAP_TIER_LIMIT} more")
        return "\n".join(lines)

    pct_done = (unlocked / total * 100) if total else 0
    parts = [
        f"## 🗺️ Roadmap — {target}",
        f"**{unlocked}/{total} unlocked ({pct_done:.0f}%) · {len(remaining)} to go**",
    ]
    if quick:
        parts.append(f"\n### 🟢 Quick wins ({len(quick)})\n{_fmt(quick)}")
    if moderate:
        parts.append(f"\n### 🟡 Moderate ({len(moderate)})\n{_fmt(moderate)}")
    if challenge:
        parts.append(f"\n### 🔴 Challenge / grind ({len(challenge)})\n{_fmt(challenge)}")
    if howto_links:
        links = "\n".join(f"- {name}: {url}" for name, url in howto_links)
        parts.append(f"\n**How to unlock the hardest:**\n{links}")
    parts.append(
        "\n_Want me to refine this? e.g. \"only the easy ones\", \"skip multiplayer\", "
        "or pick a different game._"
    )

    return {"answer": "\n".join(parts), "code_history": [code], "done": True}


def finalize_node(state: AgentState, frames: dict[str, pd.DataFrame]) -> AgentState:
    """Produce the final answer AND self-verify — concurrently.

    finalize (summarize the result) and verify (critique the result) both depend
    only on the computed result, not on each other, so we run them in parallel.
    On the happy path verification is essentially free. If the reviewer rejects
    the result (and budget remains), we set verification_error and route back to
    write_code, discarding this answer.

    The chart is NOT generated here — the UI shows the answer first, then calls
    agent.make_chart() for an answer-first experience."""
    if not state.get("last_result"):
        answer = (
            f"I couldn't compute a reliable answer after {state.get('retries', 0)} attempt(s). "
            f"Last error: {state.get('last_error') or 'unknown'}"
        )
        return {"answer": answer, "done": True}

    interp = state.get("interpretation")
    interp_line = f"INTERPRETATION (mention briefly): {interp}\n" if interp else ""
    finalize_prompt = (
        f"Today's date is {date.today().isoformat()}. Dates in 2026 on or before today are recent "
        "and valid — do NOT call them 'future dates' or data errors.\n\n"
        f"Question: {state['question']}\n\n"
        f"{interp_line}"
        f"Computed result:\n{state['last_result']}\n\n"
        "Write a clear, concise natural-language answer."
    )

    # Run finalize + verify concurrently.
    with ThreadPoolExecutor(max_workers=2) as pool:
        fin_fut = pool.submit(
            call_llm, finalize_prompt, DEEPSEEK_MODEL_FLASH, _FINALIZE_SYSTEM
        )
        ver_fut = pool.submit(_run_verify, state)
        answer = fin_fut.result()
        verify_reason = ver_fut.result()

    # Reviewer rejected the result and we still have budget → retry (discard answer).
    if verify_reason and state.get("retries", 0) < MAX_RETRIES:
        return {"verification_error": verify_reason}

    chart_pending = bool(_CHART_YES_PATTERNS.search(state.get("question", "")))

    # Proactive insight — gated (UI on, eval off) to keep the eval fast/deterministic.
    insight = None
    if state.get("with_insight"):
        raw = call_llm(
            f"Question: {state['question']}\nAnswer: {answer}",
            model=DEEPSEEK_MODEL_FLASH,
            system=_INSIGHT_SYSTEM,
        )
        if raw and raw.strip().upper() != "NONE":
            insight = raw.strip()

    return {"answer": answer, "insight": insight, "chart_pending": chart_pending, "done": True}


# ── Routing ───────────────────────────────────────────────────────────────────

def route_after_plan_code(
    state: AgentState,
) -> Literal["execute_code", "howto_search", "clarify", "chitchat", "roadmap"]:
    route = state.get("route")
    if route == "howto":
        return "howto_search"
    if route == "clarify":
        return "clarify"
    if route == "chitchat":
        return "chitchat"
    if route == "roadmap":
        return "roadmap"
    return "execute_code"


def route_after_validate(state: AgentState) -> Literal["plan_code", "finalize"]:
    """Retry on a crash/implausible result within budget; otherwise hand a valid
    result to finalize (which also self-verifies, in parallel)."""
    has_problem = state.get("last_error") or state.get("validation_error")
    if has_problem and state.get("retries", 0) < MAX_RETRIES:
        return "plan_code"
    return "finalize"


def route_after_finalize(state: AgentState) -> Literal["plan_code", "end"]:
    """finalize self-verifies in parallel; if it rejected the result and budget
    remains, retry — otherwise the answer stands."""
    if state.get("verification_error") and state.get("retries", 0) < MAX_RETRIES:
        return "plan_code"
    return "end"


# ── Graph ─────────────────────────────────────────────────────────────────────

def build_graph(frames: dict[str, pd.DataFrame]):
    g = StateGraph(AgentState)
    g.add_node("inspect_schema", lambda s: inspect_schema_node(s, frames))
    g.add_node("plan_code",      plan_code_node)
    g.add_node("clarify",        clarify_node)
    g.add_node("chitchat",       chitchat_node)
    g.add_node("roadmap",        lambda s: roadmap_node(s, frames))
    g.add_node("execute_code",   lambda s: execute_code_node(s, frames))
    g.add_node("validate_output", validate_output_node)
    g.add_node("howto_search",   howto_search_node)
    g.add_node("finalize",       lambda s: finalize_node(s, frames))

    g.add_edge(START, "inspect_schema")
    g.add_edge("inspect_schema", "plan_code")
    g.add_conditional_edges("plan_code", route_after_plan_code)
    g.add_edge("execute_code", "validate_output")
    g.add_conditional_edges("validate_output", route_after_validate,
                            {"plan_code": "plan_code", "finalize": "finalize"})
    g.add_conditional_edges("finalize", route_after_finalize,
                            {"plan_code": "plan_code", "end": END})
    g.add_edge("clarify", END)        # clarify yields a question back to the user
    g.add_edge("chitchat", END)       # chitchat yields a friendly reply itself
    g.add_edge("roadmap", END)        # roadmap yields the full plan itself
    g.add_edge("howto_search", END)   # how-to node yields the final answer itself
    return g.compile()
