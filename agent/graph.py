"""LangGraph agent core — UI-agnostic.

Flow:
    question -> inspect_schema -> planner -> (route) ->
        analysis: write_code -> execute_code -> validate_output -> (reflect?)
                  -> [retry write_code | finalize]
        howto (stretch): howto_search -> finalize
    finalize -> END
"""
from __future__ import annotations

import difflib
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
from data_layer import steam_client
from .sandbox import run_user_code
from .llm import call_llm
from .search import web_search, cached_search, cached_json


class AgentState(TypedDict, total=False):
    question: str
    steam_id: Optional[str]            # whose snapshot the sandbox loads
    history: list[dict]                # prior [{question, answer}] turns for follow-ups
    with_insight: bool                 # generate a proactive nudge (UI on, eval off)
    schema: str                        # compact description of the DataFrames
    plan: str
    route: Literal["analysis", "howto", "clarify", "chitchat", "roadmap", "audit", "timecost"]
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
    roadmap: Optional[dict]            # structured roadmap data for rich rendering
    audit: Optional[dict]              # structured audit data for rich rendering
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
ROUTE: analysis | howto | clarify | chitchat | roadmap | audit | timecost
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
  ones", "skip multiplayer", "what about <other game>"). A roadmap request that ALSO asks to
  EXCLUDE a category ("skip DLC", "skip multiplayer", "no co-op/online", "base game only") is STILL
  roadmap — the roadmap step applies that filter itself, so do NOT route clarify (or anything else)
  just because the data has no column for it. (Leave CODE empty — a dedicated step builds it.)
- audit: the user wants a FULL profile overview / report / summary across their WHOLE library
  ("audit my profile", "give me a full report", "analyze my whole profile", "profile summary").
  (Leave CODE empty — a dedicated step builds it.)
- timecost: the user asks HOW LONG it takes / how much TIME / how many HOURS to complete or 100%
  a game ("how long to 100% Hollow Knight", "time to complete X", "how many hours to finish Y").
  (Leave CODE empty — a dedicated step builds it.)

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
                 "description": <str>, "hidden": <bool>}, ... ]   # at most 60, easiest first
})

Output ONLY raw python, no markdown fences."""

_ROADMAP_FILTER_SYSTEM = """The user wants a completion roadmap and MAY have asked to exclude
achievements by THEME/category (e.g. "skip multiplayer", "no DLC", "skip online/co-op", "no PvP").
You are given the user's request and a numbered list of achievements (name — description).

Reply with ONLY the numbers to REMOVE because they match an exclusion the user asked for,
comma-separated (e.g. "2, 5, 9"). If the request asks for NO such exclusion, reply EXACTLY: NONE.

Rules: judge by name + description. Do NOT remove for difficulty/rarity ("easy"/"hard") — that's
handled elsewhere. When unsure whether an achievement matches the exclusion, KEEP it (don't list it).
Never remove everything."""

# The audit is a battery of INDEPENDENT analyses. Rather than have the model write
# one huge script in a single ~60s+ call, each group below is generated and run
# CONCURRENTLY (see audit_node) — same agent-writes-the-code design, far lower
# wall-clock. Each group keeps the same per-call retry for reliability.
_AUDIT_BASE = """You are a pandas expert computing part of a Steam profile audit.

Pre-loaded (do NOT redefine, do NOT import): games, achievements, player_unlocks, pd, np, json.
CRITICAL column meanings:
- player_unlocks.achieved = whether THIS PLAYER unlocked the achievement.
- achievements.rarity_pct = % of ALL players who unlocked it (float, may be NaN).
- unlock_time = Unix seconds (use pd.to_datetime(x, unit='s') for dates).
Only count games that HAVE achievements where a completion % is needed.
"Started" = >=1 achievement unlocked. Output ONLY raw python (no markdown fences);
assign the result via `result = json.dumps({...})` with EXACTLY the keys below."""

_AUDIT_GROUPS: list[tuple[str, str, str]] = [   # (name, model, spec)
    ("overview", DEEPSEEK_MODEL_FLASH, """{
  "total_unlocked":   <int achievements the player has unlocked>,
  "total_achievements": <int total achievements across games that have them>,
  "overall_pct":      <float total_unlocked / total_achievements * 100>,
  "games_total":      <int owned games>,
  "games_started":    <int games with >=1 unlock>,
  "games_completed":  <int games at 100%>
}"""),
    ("highlights", DEEPSEEK_MODEL_PRO, """{
  "rarest":   {"name": <display_name>, "rarity_pct": <float>, "game": <game name>},  # rarest UNLOCKED
  "easy_wins":[{"name":..., "rarity_pct":..., "game":...}, ...]   # 5 LOCKED achievements, highest rarity_pct
}"""),
    ("progress", DEEPSEEK_MODEL_PRO, """{
  "abandoned":[{"game":..., "pct": <float>, "remaining": <int>}, ...],  # started, <10% complete, up to 5
  "focus":    {"game":..., "remaining": <int>, "pct": <float>},   # started, <100%, FEWEST remaining
  "momentum": {"last_unlock": <"YYYY-MM-DD" of most recent unlock>, "unlocks_last_30d": <int>}
}
For momentum use the player's unlocked rows only; "last 30 days" = within 30 days of the latest unlock."""),
    ("chart", DEEPSEEK_MODEL_FLASH, """{
  "completion_by_game":[{"game":..., "pct": <float>}, ...]   # top 10 games by completion %, for a chart
}"""),
]

_HOWTO_SYSTEM = """You are a Steam achievement guide assistant.
Given the user's question and a few web search snippets, write a practical,
concise how-to answer: the concrete steps or tips to unlock the achievement.
Base it on the snippets — if they're thin or conflicting, say what's known and
don't invent specifics. Keep it under ~6 sentences, then end with a 'Sources:'
list of the URLs you actually used."""

_TIMECOST_NAME_SYSTEM = """You identify the single video-game title the user wants a
time-to-complete estimate for. Use the conversation history to resolve indirect references
("that one", "it", "how about it"). Reply with ONLY the game's name and nothing else — no quotes,
no extra words. If no specific game can be determined, reply EXACTLY: NONE."""

_TIMECOST_SYNTH_SYSTEM = """You extract the time to FULLY complete (100% / all achievements) a game
from web snippets (often HowLongToBeat). Given the game name and a few snippets:
- Use the hours figures in the snippets for the FULL base game's 100%/completionist/all-achievements
  time. Prefer a 'Completionist' figure; otherwise use all-achievements/all-campaigns completion times.
- IGNORE figures that are clearly for a DLC/episode only, or a record speedrun — they're not typical.
- If several reasonable figures appear, give a representative RANGE (e.g. "around 9–17 hours").
- Answer in ONE concise sentence with the hours. Only reply EXACTLY "NONE" if the snippets contain
  NO time-to-complete figure at all. Never invent numbers not supported by the snippets."""

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
        # Surface WHY (timeout vs crash vs no-file) — otherwise chart failures are
        # invisible in prod. Cheap log, no behavior change.
        print(f"[chart] generation failed: {error or 'chart file not written'}")
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
            elif "audit" in low:
                route = "audit"
            elif "timecost" in low:
                route = "timecost"
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


def _game_stats(appid, frames: dict[str, pd.DataFrame]) -> tuple[int, int, float]:
    """(total achievements, unlocked by player, playtime hours) for one appid."""
    ach, pu, g = frames["achievements"], frames["player_unlocks"], frames["games"]
    total = int((ach["appid"] == appid).sum())
    unlocked = int(((pu["appid"] == appid) & pu["achieved"]).sum())
    row = g[g["appid"] == appid]
    playtime = round(float(row.iloc[0]["playtime"]) / 60, 1) if not row.empty else 0.0
    return total, unlocked, playtime


def _match_owned_game(question: str, games_df: pd.DataFrame):
    """Deterministically resolve an EXPLICITLY-named owned game from the question:
    the longest owned game name that appears as a substring (case-insensitive).
    Longest wins so 'Left 4 Dead 2' beats 'Left 4 Dead'. Returns (name, appid) or
    None — None falls back to the LLM for typos / pronoun follow-ups."""
    ql = (question or "").lower()
    best = None
    for name, appid in zip(games_df["name"].astype(str), games_df["appid"]):
        if len(name) >= 3 and name.lower() in ql:
            if best is None or len(name) > len(best[0]):
                best = (name, appid)
    return best


def _fuzzy_owned(title: str, games_df: pd.DataFrame, threshold: float = 0.82):
    """Best owned-game match for a short extracted TITLE — substring either way or a
    high fuzzy ratio (catches typos like 'Left 4 Ded 2'). Returns (name, appid) or
    None when nothing is close (→ the user means a game they don't own)."""
    tl = (title or "").lower().strip()
    if not tl:
        return None
    best_score, best = 0.0, None
    for name, appid in zip(games_df["name"].astype(str), games_df["appid"]):
        nl = name.lower()
        score = difflib.SequenceMatcher(None, tl, nl).ratio()
        if tl in nl or nl in tl:
            score = max(score, 0.9)
        if score > best_score:
            best_score, best = score, (name, appid)
    return best if best_score >= threshold else None


def time_estimate_node(state: AgentState, frames: dict[str, pd.DataFrame]) -> AgentState:
    """Estimate time-to-100% a game: COMMUNITY average via a cached Tavily search,
    grounded with the player's OWN pace from snapshot data. Terminal node (-> END).

    Game resolution is Python-first (deterministic substring match on owned names);
    a cheap LLM call only extracts the title for typos, pronoun follow-ups, or games
    the player does NOT own. Owned stats come straight from the frames (no hallucination);
    unowned games get the community average only (no playtime → no personal estimate)."""
    target = None
    owned = False
    total = unlocked = 0
    playtime = 0.0

    matched = _match_owned_game(state["question"], frames["games"])
    if matched:
        owned, (target, appid) = True, matched
        total, unlocked, playtime = _game_stats(appid, frames)
    else:
        # Extract the title (handles typos, follow-ups, and unowned games).
        prompt = (
            f"{_format_history(state)}"
            f"Question: {state['question']}"
        )
        title = call_llm(prompt, model=DEEPSEEK_MODEL_FLASH, system=_TIMECOST_NAME_SYSTEM).strip()
        if title and title.upper() != "NONE":
            m2 = _fuzzy_owned(title, frames["games"])
            if m2:  # a game they own (matched through a typo/partial name)
                owned, (target, appid) = True, m2
                total, unlocked, playtime = _game_stats(appid, frames)
            else:   # a real game they don't own → community-only
                target = title

    if not target:
        suggestions = _suggest_owned_games(state["question"], frames["games"])
        hint = (f" Did you mean {', '.join(suggestions)}?" if suggestions else "")
        return {
            "answer": ("Tell me which game you'd like a time-to-complete estimate for, "
                       "e.g. \"how long to 100% Hollow Knight?\"." + hint),
            "route": "timecost", "done": True,
        }

    remaining = max(total - unlocked, 0)

    # Personal pace (grounded) — owned games with enough signal to be honest.
    personal = ""
    if owned and total and unlocked >= total:
        personal = f"You've already 100%-ed **{target}** 🎉 — nothing left to grind."
    elif owned and playtime >= 1 and unlocked >= 3:
        rate = unlocked / playtime  # achievements per hour, this player
        if rate > 0:
            personal = (f"At **your** pace ({unlocked}/{total} in {playtime:.0f}h ≈ {rate:.1f}/hr), "
                        f"roughly **{remaining / rate:.0f}h** left for the remaining {remaining}.")

    # Community average via CACHED Tavily (shared across users by game → bounded cost).
    results = cached_search(
        f"timecost:{target.lower()}",
        f"howlongtobeat {target} completionist 100% all achievements hours",
        max_results=5,
    )
    community = ""
    if results:
        context = "\n\n".join(f"[{i}] {r['title']}\n{r['url']}\n{r['content']}"
                              for i, r in enumerate(results, 1))
        synth = call_llm(f"Game: {target}\n\nWeb results:\n{context}",
                         model=DEEPSEEK_MODEL_FLASH, system=_TIMECOST_SYNTH_SYSTEM).strip()
        if synth and synth.upper() != "NONE":
            community = synth

    parts = [f"### ⏱️ Time to 100% {target}"]
    if community:
        parts.append(f"**Community average:** {community}")

    if owned:
        if personal:
            parts.append(personal)
        if not community and not personal:
            parts.append(f"You're at {unlocked}/{total} ({remaining} left), but I couldn't find "
                         "community completion-time data and don't have enough of your playtime to estimate.")
    else:
        if community:
            parts.append(f"_You don't own **{target}** yet, so there's no personalized estimate — "
                         "that's the community average._")
        else:
            parts.append(f"I couldn't find community completion-time data for **{target}**, "
                         "and you don't own it so I can't estimate from your playtime.")

    return {"answer": "\n\n".join(parts), "route": "timecost", "sources": results, "done": True}


# ── Roadmap (flagship) ────────────────────────────────────────────────────────

_TIER_QUICK_MIN    = 50.0   # rarity_pct >= 50 → quick win (most players have it)
_TIER_MODERATE_MIN = 15.0   # 15–50 → moderate; < 15 (or unknown) → challenge/grind
_ROADMAP_MAX_HOWTO = 2      # bound web lookups to the hardest few
_ROADMAP_TIER_LIMIT = 15    # show at most N per tier


def _rarity(a: dict) -> Optional[float]:
    """Parse rarity_pct, mapping missing/NaN to None."""
    try:
        r = float(a.get("rarity_pct"))
        return None if r != r else r  # NaN != NaN
    except (TypeError, ValueError):
        return None


_COMMON_WORDS = {"the", "a", "of", "and", "to", "ii", "iii", "iv", "edition",
                 "definitive", "game", "games", "online", "hd", "remastered"}


def _suggest_owned_games(target: str, games_df: pd.DataFrame, n: int = 3) -> list[str]:
    """Find owned games with names similar to `target` (for "did you mean…?").
    Uses word overlap (so 'Forza Horizon 6' → 'Forza Horizon 4') + fuzzy ratio."""
    target_l = (target or "").lower()
    target_words = {w for w in re.findall(r"[a-z0-9]+", target_l)
                    if w not in _COMMON_WORDS and len(w) > 2}
    scored = []
    for name in games_df["name"].astype(str):
        nl = name.lower()
        shared = target_words & set(re.findall(r"[a-z0-9]+", nl))
        ratio = difflib.SequenceMatcher(None, target_l, nl).ratio()
        if shared or ratio > 0.5:
            scored.append((len(shared) + ratio, name))
    scored.sort(reverse=True)
    out: list[str] = []
    for _, name in scored:
        if name not in out:
            out.append(name)
        if len(out) >= n:
            break
    return out


def _fetch_schema_and_rarity(appid: int) -> Optional[dict]:
    """Live-fetch a game's full achievement schema + global rarity (both endpoints
    work for ANY appid). Returns {"achievements": [{name, rarity_pct, description,
    hidden}]} or None if the game exposes no achievements."""
    try:
        schema = steam_client.get_schema_for_game(appid)
        gpct = steam_client.get_global_achievement_pct(appid)
    except Exception:
        return None
    ach_list = (
        (schema.get("game", {}).get("availableGameStats", {}) or {}).get("achievements", [])
    )
    if not ach_list:
        return None
    pct = {p.get("name"): p.get("percent")
           for p in gpct.get("achievementpercentages", {}).get("achievements", [])}
    return {"achievements": [
        {
            "name": a.get("displayName") or a.get("name"),
            "rarity_pct": pct.get(a.get("name")),
            "description": a.get("description", ""),
            "hidden": bool(a.get("hidden", 0)),
        }
        for a in ach_list
    ]}


def _unowned_roadmap_data(name: str) -> Optional[dict]:
    """Build roadmap data for a game the player does NOT own. Resolves name->appid
    via Steam store search, then live-fetches the schema + rarity — ALL achievements
    count as locked (the player owns none). Cached + shared by game.

    Sanctioned exception to the snapshot rule (like the time-estimate web search):
    the snapshot only holds owned games, so an unowned roadmap MUST fetch live.
    Returns {target, total, remaining} or None if unresolvable / no achievements.
    Semantic refine filters ("skip multiplayer", "no DLC") are applied separately by
    `_apply_unowned_filter` in the node, mirroring the owned path's code-gen filters."""
    apps = cached_json(f"appsearch:{name.lower().strip()}", lambda: steam_client.search_app(name))
    if not apps:
        return None
    appid = apps[0]["appid"]
    resolved = apps[0].get("name") or name

    payload = cached_json(f"schema:{appid}", lambda: _fetch_schema_and_rarity(appid))
    achs = (payload or {}).get("achievements") or []
    if not achs:
        return None
    # Easiest-first (highest global rarity %), NaN/unknown last — matches owned path.
    achs.sort(key=lambda a: (_rarity(a) is None, -(_rarity(a) or 0)))
    return {"target": resolved, "total": len(achs), "remaining": achs}


def _apply_unowned_filter(question: str, achievements: list[dict]) -> list[dict]:
    """Apply a SEMANTIC exclusion filter (e.g. "skip multiplayer", "no DLC") to a
    fetched unowned-game achievement list via one Flash call — this gives unowned
    roadmaps the same refine ability the owned (sandbox code-gen) path has. Returns
    the list unchanged when there's no exclusion. Best-effort: any failure, an empty
    parse, or an attempt to drop everything falls back to the full list."""
    if not achievements:
        return achievements
    listing = "\n".join(
        f"{i}. {a['name']}" + (f" — {a.get('description', '')}" if a.get("description") else "")
        for i, a in enumerate(achievements, 1)
    )
    try:
        resp = call_llm(f"Request: {question}\n\nAchievements:\n{listing}",
                        model=DEEPSEEK_MODEL_FLASH, system=_ROADMAP_FILTER_SYSTEM).strip()
    except Exception:
        return achievements
    if not resp or resp.upper() == "NONE":
        return achievements
    drop = {int(t) - 1 for t in re.findall(r"\d+", resp)}
    drop = {i for i in drop if 0 <= i < len(achievements)}
    if not drop or len(drop) >= len(achievements):
        return achievements  # nothing matched, or it tried to drop everything → ignore
    return [a for i, a in enumerate(achievements) if i not in drop]


# ── Phase tagging (Roadmap v2) ────────────────────────────────────────────────
# The achievement LIST stays grounded (sandbox); these CATEGORY/missable tags are
# an LLM interpretation, so the UI labels the result a "suggested plan".
_ROADMAP_PHASE_MAX = 60
_PHASE_CATEGORIES = ["story", "collectible", "combat", "skill", "grind", "multiplayer", "dlc", "misc"]
_PHASE_DEFS = [
    ("story", "📖 Story & progression"),
    ("collectible", "🗺️ Collectibles & exploration"),
    ("combat", "⚔️ Combat & encounters"),
    ("skill", "🎯 Skill challenges"),
    ("grind", "⏳ The grind"),
    ("multiplayer", "👥 Multiplayer"),
    ("dlc", "🧩 DLC"),
    ("misc", "✦ Everything else"),
]
_ROADMAP_PHASE_SYSTEM = """You sort video-game achievements into a completion game-plan.
For EACH numbered achievement output one line: "<N>: <category>[ missable]".
category is EXACTLY one of: story, collectible, combat, skill, grind, multiplayer, dlc, misc.
- story: main-story / campaign / progression milestones.
- collectible: find/collect/explore/discover items, areas, or lore.
- combat: defeat enemies/bosses, kills, weapon use.
- skill: flawless/hard execution (no damage, speedrun, high score, hardest difficulty).
- grind: large repeated counts / long playtime / level or currency farming.
- multiplayer: online/co-op/PvP/ranked.
- dlc: tied to DLC/expansion content.
- misc: doesn't clearly fit the others.
Append " missable" ONLY if the text strongly implies it can be permanently missed
(e.g. "in a single playthrough", "before <point>", "without dying/killing anyone").
Use ONLY the name + description. Output ONLY the lines."""


def _tag_phases(remaining: list, target: str) -> list:
    """Group the (grounded) remaining achievements into an ordered, phase-based plan
    via one Flash classification pass. Missables get a synthetic first phase (timing
    matters most); the rest fall into category phases in a sensible completion order.
    Best-effort — any failure leaves a single 'Everything else' phase."""
    items = remaining[:_ROADMAP_PHASE_MAX]
    listing = "\n".join(
        f"{i}. {a.get('name', '')}" +
        (f" — {a.get('description', '').strip()}" if a.get("description") and not a.get("hidden") else "")
        for i, a in enumerate(items, 1)
    )
    tags: dict[int, tuple[str, bool]] = {}
    try:
        raw = call_llm(f"Game: {target}\n\nAchievements:\n{listing}",
                       model=DEEPSEEK_MODEL_FLASH, system=_ROADMAP_PHASE_SYSTEM)
        for line in raw.splitlines():
            m = re.match(r"\s*(\d+)\s*[:.\)]\s*([a-zA-Z]+)(\s+missable)?", line.strip())
            if not m:
                continue
            cat = m.group(2).lower()
            tags[int(m.group(1)) - 1] = (cat if cat in _PHASE_CATEGORIES else "misc", bool(m.group(3)))
    except Exception:
        pass

    for i, a in enumerate(items):
        cat, missable = tags.get(i, ("misc", False))
        a["category"], a["missable"] = cat, missable

    phases = []
    missables = [a for a in items if a.get("missable")]
    if missables:
        phases.append({"key": "missable", "title": "⚠️ Missables — do these at the right time",
                       "achievements": missables, "warn": True})
    for key, title in _PHASE_DEFS:
        grp = [a for a in items if a.get("category") == key and not a.get("missable")]
        if grp:
            phases.append({"key": key, "title": title, "achievements": grp})
    return phases


def roadmap_node(state: AgentState, frames: dict[str, pd.DataFrame]) -> AgentState:
    """Flagship: build a VERIFIED, tiered completion roadmap. For OWNED games the
    achievement data comes from the sandbox (real, still-locked achievements with
    real rarity), so it cannot be hallucinated. For a game the player does NOT own,
    it live-fetches the full schema/rarity (all locked). Ends with a refine invite."""
    games_df = frames["games"]
    q = state["question"]

    # Decide ownership UP-FRONT. If the user named a specific game they do NOT own,
    # the sandbox can't help (it only has owned games) — it would crash or fabricate
    # data — so take the live-fetch path. Owned games, the whole library, and refine
    # follow-ups all keep the proven sandbox path. The extra title-extraction call
    # only fires when no owned game name appears verbatim in the question.
    unowned_target = None
    if not _match_owned_game(q, games_df):
        title = call_llm(
            f"{_format_history(state)}Question: {q}",
            model=DEEPSEEK_MODEL_FLASH, system=_TIMECOST_NAME_SYSTEM,
        ).strip()
        if title and title.upper() != "NONE" and not _fuzzy_owned(title, games_df):
            unowned_target = title

    code = ""
    unowned = False
    if unowned_target:
        fetched = _unowned_roadmap_data(unowned_target)
        if not fetched:
            suggestions = _suggest_owned_games(unowned_target, games_df)
            hint = f" Did you mean one you own — {', '.join(suggestions)}?" if suggestions else ""
            return {
                "answer": (f"I couldn't find **{unowned_target}** with achievement data on "
                           f"Steam.{hint}"),
                "done": True,
            }
        unowned = True
        target, total, unlocked = fetched["target"], fetched["total"], 0
        # Honor semantic refine filters ("skip multiplayer", "no DLC") on unowned
        # games too — the sandbox path gets this from code-gen; here it's a Flash pass.
        remaining = _apply_unowned_filter(q, fetched["remaining"])
    else:
        prompt = (
            f"Schema:\n{state['schema']}\n\n"
            f"{_format_history(state)}"
            f"Question: {q}"
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
        if total == 0:
            # Not owned AND not findable on Steam (or it has no achievements).
            suggestions = _suggest_owned_games(target, frames["games"])
            if suggestions:
                answer = (
                    f"I couldn't find **{target}** in your library or on Steam. "
                    f"Did you mean one of these you own — {', '.join(suggestions)}? "
                    "Ask me for a roadmap to one of those."
                )
            else:
                answer = (
                    f"I couldn't find a game called **{target}** with achievement data on "
                    "Steam. Try the exact title, or a game you own."
                )
        elif unlocked >= total:
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
    true_remaining = (total - unlocked) if total else len(remaining)
    shown = len(remaining)
    # Decorate the DISPLAYED title for unowned games (keep `target` clean for the
    # how-to search above). Surfaces in both the markdown and the React card.
    display_target = f"{target} (not in your library)" if unowned else target
    parts = [
        f"## 🗺️ Roadmap — {display_target}",
        f"**{unlocked}/{total} unlocked ({pct_done:.0f}%) · {true_remaining} to go**",
    ]
    if shown < true_remaining:
        parts.append(
            f"_Showing the {shown} easiest of {true_remaining} remaining — "
            "ask for more or a specific tier (e.g. \"show the hard ones\")._"
        )
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

    # Structured data for rich (React) rendering; markdown `answer` is the fallback.
    roadmap_data = {
        "target": display_target,
        "total": total,
        "unlocked": unlocked,
        "pct_done": round(pct_done, 1),
        "remaining": true_remaining,
        "shown": shown,
        "tiers": {
            "quick": quick[:_ROADMAP_TIER_LIMIT],
            "moderate": moderate[:_ROADMAP_TIER_LIMIT],
            "challenge": challenge[:_ROADMAP_TIER_LIMIT],
        },
        "tier_counts": {"quick": len(quick), "moderate": len(moderate), "challenge": len(challenge)},
        "howto": [{"name": n, "url": u} for n, u in howto_links],
        # Suggested phase plan (LLM-tagged on top of the grounded list).
        "phases": _tag_phases(remaining, target),
    }
    return {"answer": "\n".join(parts), "roadmap": roadmap_data,
            "code_history": [code] if code else [], "done": True}


# ── Profile Audit (flagship) ──────────────────────────────────────────────────

_AUDIT_NARRATIVE_SYSTEM = """You are a Steam achievement coach. Given a JSON profile summary, write
ONE warm, encouraging opening sentence for an audit report (no stats dumps, no lists — just a
human framing of where the player is at). Recent 2026 dates are valid, not errors."""


def _audit_chart(cbg: list, frames: dict, steam_id: Optional[str]) -> Optional[str]:
    """Render the 'completion % by top games' bar chart for the audit."""
    if not cbg:
        return None
    _CHARTS_DIR.mkdir(parents=True, exist_ok=True)
    cp = str(_CHARTS_DIR / f"{uuid.uuid4().hex}.png")
    names = [d.get("game", "") for d in cbg][:10]
    pcts  = [d.get("pct", 0) for d in cbg][:10]
    code = (
        f"CHART_PATH = {cp!r}\nnames = {names!r}\npcts = {pcts!r}\n"
        "y = list(range(len(names)))\n"
        "fig, ax = plt.subplots(figsize=(10, 6))\n"
        "ax.barh(y, pcts, color='steelblue')\n"
        "ax.set_yticks(y); ax.set_yticklabels(names); ax.invert_yaxis()\n"
        "ax.set_xlabel('Completion %'); ax.set_title('Top games by completion')\n"
        "plt.tight_layout(); plt.savefig(CHART_PATH, bbox_inches='tight', dpi=120); plt.close()\n"
        "result = CHART_PATH\n"
    )
    _, err = run_user_code(code, frames, steam_id)
    return cp if (not err and Path(cp).exists()) else None


def _audit_group(state: AgentState, frames: dict[str, pd.DataFrame], model: str, spec: str) -> tuple[dict, str]:
    """Generate + run ONE audit metric-group (the agent still writes the code).
    `model` is per-group: simple groups use FLASH (faster+cheaper), complex ones PRO.
    Keeps a 2-try retry on a crash / invalid JSON. Returns (partial_data, code);
    partial_data is {} on failure so the rest of the audit degrades gracefully."""
    system = f"{_AUDIT_BASE}\n\nReturn EXACTLY these keys:\n{spec}"
    code, err_ctx = "", ""
    for _ in range(2):
        prompt = f"Schema:\n{state['schema']}\n\n{err_ctx}Compute the requested audit metrics."
        raw = call_llm(prompt, model=model, system=system)
        code = _extract_code(raw)
        result, error = run_user_code(code, frames, state.get("steam_id"))
        if error:
            err_ctx = f"Your previous code FAILED — fix it:\n{error}\n\n"
            continue
        try:
            return json.loads(result), code
        except Exception as exc:
            err_ctx = f"Your previous result was not valid JSON ({exc}). Return json.dumps(...).\n\n"
    return {}, code


def audit_node(state: AgentState, frames: dict[str, pd.DataFrame]) -> AgentState:
    """Flagship: autonomous profile audit. The agent writes the analysis code, but
    the battery is split into INDEPENDENT metric groups generated + run CONCURRENTLY
    (instead of one ~60s+ mega-call), then synthesized into a structured, charted
    report — multi-step analysis orchestrated behind a single request."""
    # Generate every group in parallel; each keeps its own retry. Threads give real
    # parallelism here because both the LLM HTTP call and the sandbox subprocess
    # release the GIL while they wait.
    with ThreadPoolExecutor(max_workers=len(_AUDIT_GROUPS)) as ex:
        results = list(ex.map(
            lambda g: _audit_group(state, frames, g[1], g[2]), _AUDIT_GROUPS
        ))

    data: dict = {}
    codes: list[str] = []
    for partial, code in results:
        data.update(partial)
        if code:
            codes.append(code)

    if not data:
        return {
            "answer": "I couldn't build an audit right now — please try again.",
            "code_history": codes,
            "done": True,
        }

    # Short LLM narrative intro (today's date injected so 2026 dates aren't flagged).
    try:
        intro = call_llm(
            f"Today is {date.today().isoformat()}.\nProfile summary JSON:\n{json.dumps(data)[:1500]}",
            model=DEEPSEEK_MODEL_FLASH, system=_AUDIT_NARRATIVE_SYSTEM,
        ).strip()
    except Exception:
        intro = ""

    chart_path = _audit_chart(data.get("completion_by_game") or [], frames, state.get("steam_id"))

    g = data.get
    parts = ["## 🔍 Profile Audit"]
    if intro:
        parts.append(f"\n{intro}")
    parts.append(
        f"\n**Overview** — {g('total_unlocked', 0)}/{g('total_achievements', 0)} achievements "
        f"({g('overall_pct', 0):.1f}%) · {g('games_started', 0)}/{g('games_total', 0)} games started · "
        f"{g('games_completed', 0)} fully completed"
    )

    rarest = data.get("rarest") or {}
    if rarest.get("name"):
        r = rarest.get("rarity_pct")
        rp = f"{r:.1f}%" if isinstance(r, (int, float)) else "?"
        parts.append(f"\n**🏆 Rarest flex** — {rarest['name']} ({rp}) in {rarest.get('game', '?')}")

    easy = data.get("easy_wins") or []
    if easy:
        lines = "\n".join(
            f"- **{a.get('name')}** ({a.get('rarity_pct'):.0f}% have it) — {a.get('game')}"
            for a in easy if isinstance(a.get("rarity_pct"), (int, float))
        )
        if lines:
            parts.append(f"\n**🟢 Easy wins to grab**\n{lines}")

    abandoned = data.get("abandoned") or []
    if abandoned:
        lines = "\n".join(
            f"- {a.get('game')} — {a.get('pct', 0):.0f}% ({a.get('remaining', 0)} left)"
            for a in abandoned
        )
        parts.append(f"\n**💤 Stalled games**\n{lines}")

    mom = data.get("momentum") or {}
    if mom.get("last_unlock"):
        parts.append(
            f"\n**📈 Momentum** — last unlock {mom['last_unlock']} · "
            f"{mom.get('unlocks_last_30d', 0)} in the last 30 days"
        )

    focus = data.get("focus") or {}
    if focus.get("game"):
        parts.append(
            f"\n**🎯 Recommended focus** — {focus['game']}, only {focus.get('remaining', 0)} "
            f"achievements left ({focus.get('pct', 0):.0f}%)"
        )
        parts.append(f"\n_Want a roadmap for {focus['game']}? Just ask._")

    # Structured data + intro for rich (React) rendering; markdown `answer` is the fallback.
    audit_data = dict(data)
    audit_data["intro"] = intro
    return {"answer": "\n".join(parts), "audit": audit_data,
            "chart_path": chart_path, "code_history": codes, "done": True}


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
) -> Literal["execute_code", "howto_search", "clarify", "chitchat", "roadmap", "audit", "time_estimate"]:
    route = state.get("route")
    if route == "howto":
        return "howto_search"
    if route == "clarify":
        return "clarify"
    if route == "chitchat":
        return "chitchat"
    if route == "roadmap":
        return "roadmap"
    if route == "audit":
        return "audit"
    if route == "timecost":
        return "time_estimate"
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
    g.add_node("audit",          lambda s: audit_node(s, frames))
    g.add_node("execute_code",   lambda s: execute_code_node(s, frames))
    g.add_node("validate_output", validate_output_node)
    g.add_node("howto_search",   howto_search_node)
    g.add_node("time_estimate",  lambda s: time_estimate_node(s, frames))
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
    g.add_edge("audit", END)          # audit yields the full report itself
    g.add_edge("howto_search", END)   # how-to node yields the final answer itself
    g.add_edge("time_estimate", END)  # time-estimate node yields the final answer itself
    return g.compile()
