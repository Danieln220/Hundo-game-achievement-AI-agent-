"""Deterministic fast-path (Step 19.3) — instant answers for the common shapes.

The ~15 most common question shapes are answered straight from the snapshot in
pure pandas: zero LLM calls, zero sandbox, sub-second, cannot hallucinate. The
LLM was never the source of these answers anyway — it wrote code that computed
them from the frames; here the computation is written once, permanently.

Matching is CONSERVATIVE by design: a false negative just falls through to the
full agent (slower but correct), while a false match would answer the wrong
question fast — so patterns are strict, and anything compound, history-
dependent ("what about…", bare pronouns) or scoped to an unresolved game
returns None and takes the normal pipeline.

fast_answer() returns a run()-compatible result dict, or None to fall through.
The API calls this BEFORE run(); run() itself stays pure so the golden eval
keeps measuring the full agent.
"""
from __future__ import annotations

import re
from typing import Callable, Optional

import pandas as pd

from config import STEAM_ID
from data_layer.snapshot import load_frames
from .graph import _match_owned_game

# Anything here means "not a simple lookup" — comparisons, guides, plans, time
# estimates, follow-ups, and bare pronouns that need conversation history.
_FALLTHROUGH = re.compile(
    r"\b(roadmap|road\s*map|plan|audit|report|how\s+long|how\s+many\s+hours|how\s+do|"
    r"how\s+to|guide|why|compare|versus|vs\.?|friends?|what\s+about|how\s+about|"
    r"instead|it|that|this\s+one|those|them|they)\b",
    re.IGNORECASE,
)


def _p(v: float) -> str:
    """75.0 -> '75%', 78.57 -> '78.6%' — percentages without trailing zeros."""
    s = f"{float(v):.1f}".rstrip("0").rstrip(".")
    return f"{s}%"


def _build_ctx(frames: dict) -> dict:
    games, ach, pu = frames["games"], frames["achievements"], frames["player_unlocks"]
    m = ach.merge(pu[["appid", "api_name", "achieved", "unlock_time"]],
                  on=["appid", "api_name"], how="left")
    m["achieved"] = m["achieved"].fillna(False).astype(bool)
    per = m.groupby("appid").agg(total=("achieved", "size"),
                                 unlocked=("achieved", "sum")).reset_index()
    per["pct"] = per["unlocked"] / per["total"] * 100
    gname = dict(zip(games["appid"], games["name"].astype(str)))
    return {"games": games, "m": m, "per": per, "gname": gname}


def _scope(q: str, ctx: dict):
    """Resolve an optional per-game scope. Returns (appid|None, q_without_name, ok).
    ok=False when the question targets something ('in/for/on X') that is NOT an
    owned game — those must fall through to the agent (which handles unowned
    games properly) instead of being answered library-wide by mistake."""
    hit = _match_owned_game(q, ctx["games"])
    if hit:
        name, appid = hit
        return appid, q.lower().replace(name.lower(), " "), True
    if re.search(r"\b(?:in|for|on)\s+(?!my\b|all\b|total\b|the\b|steam\b)\w", q, re.IGNORECASE):
        return None, q, False
    return None, q, True


def _g(ctx: dict, appid) -> str:
    return ctx["gname"].get(appid, str(appid))


# ── shape computes ────────────────────────────────────────────────────────────

def _rarest(q, ctx):
    appid, _, ok = _scope(q, ctx)
    if not ok:
        return None
    have = ctx["m"][ctx["m"]["achieved"] & ctx["m"]["rarity_pct"].notna()]
    if appid is not None:
        have = have[have["appid"] == appid]
    if have.empty:
        return None
    n_match = re.search(r"\btop\s*(\d+)\b|\b(\d+)\s+rarest\b", q, re.IGNORECASE)
    n = min(int(n_match.group(1) or n_match.group(2)), 10) if n_match else 1
    rows = have.sort_values("rarity_pct").head(n)
    if n == 1:
        r = rows.iloc[0]
        where = f" in **{_g(ctx, r['appid'])}**"
        return (f"Your rarest achievement is **{r['display_name']}**{where} — "
                f"only {_p(r['rarity_pct'])} of players have it.")
    lines = [f"- **{r.display_name}** ({_p(r.rarity_pct)} of players) — {_g(ctx, r.appid)}"
             for r in rows.itertuples(index=False)]
    return f"Your {len(lines)} rarest achievements:\n" + "\n".join(lines)


def _most_common(q, ctx):
    appid, _, ok = _scope(q, ctx)
    if not ok:
        return None
    have = ctx["m"][ctx["m"]["achieved"] & ctx["m"]["rarity_pct"].notna()]
    if appid is not None:
        have = have[have["appid"] == appid]
    if have.empty:
        return None
    r = have.loc[have["rarity_pct"].idxmax()]
    return (f"Your most common achievement is **{r['display_name']}** in "
            f"**{_g(ctx, r['appid'])}** — {_p(r['rarity_pct'])} of players have it.")


def _recent(q, ctx):
    appid, _, ok = _scope(q, ctx)
    if not ok:
        return None
    u = ctx["m"][ctx["m"]["achieved"] & (ctx["m"]["unlock_time"] > 0)]
    if appid is not None:
        u = u[u["appid"] == appid]
    if u.empty:
        return None
    r = u.loc[u["unlock_time"].idxmax()]
    when = pd.to_datetime(int(r["unlock_time"]), unit="s").strftime("%Y-%m-%d")
    return (f"Your most recent unlock is **{r['display_name']}** in "
            f"**{_g(ctx, r['appid'])}**, on {when}.")


def _hidden(q, ctx):
    m = ctx["m"]
    if re.search(r"\bunlock", q, re.IGNORECASE):
        n = int((m["hidden"] & m["achieved"]).sum())
        return f"You've unlocked **{n}** hidden-description achievements."
    n = int(m["hidden"].sum())
    return f"**{n}** achievements across your games have hidden (spoiler) descriptions."


def _perfect(q, ctx):
    per, done = ctx["per"], None
    done = per[(per["unlocked"] >= per["total"]) & (per["total"] > 0)]
    if done.empty:
        close = per[per["unlocked"] > 0].sort_values("pct", ascending=False)
        if close.empty:
            return None
        c = close.iloc[0]
        return (f"No 100% games yet — your closest is **{_g(ctx, c['appid'])}** at "
                f"{_p(c['pct'])} ({int(c['unlocked'])}/{int(c['total'])}).")
    lines = [f"- **{_g(ctx, r.appid)}** ({int(r.total)}/{int(r.total)})"
             for r in done.itertuples(index=False)]
    return f"You've fully completed {len(lines)} game(s):\n" + "\n".join(lines)


def _closest(q, ctx):
    per = ctx["per"]
    cand = per[(per["unlocked"] > 0) & (per["pct"] < 100)].sort_values("pct", ascending=False).head(3)
    if cand.empty:
        return None
    lines = [f"- **{_g(ctx, r.appid)}** — {_p(r.pct)} ({int(r.unlocked)}/{int(r.total)}, "
             f"{int(r.total - r.unlocked)} to go)" for r in cand.itertuples(index=False)]
    return "Closest to 100%:\n" + "\n".join(lines)


def _next_target(q, ctx):
    per = ctx["per"]
    cand = per[(per["unlocked"] > 0) & (per["pct"] < 100)].copy()
    if cand.empty:
        return None
    cand["remaining"] = cand["total"] - cand["unlocked"]
    cand = cand.sort_values("remaining").head(3)
    r0 = cand.iloc[0]
    out = (f"**{_g(ctx, r0['appid'])}** is your fastest 100% — only "
           f"**{int(r0['remaining'])}** achievements left ({_p(r0['pct'])} done).")
    if len(cand) > 1:
        rest = " · ".join(f"{_g(ctx, r.appid)} ({int(r.remaining)} left)"
                          for r in cand.iloc[1:].itertuples(index=False))
        out += f"\n\nRunners-up: {rest}."
    return out


def _easy_wins(q, ctx):
    appid, _, ok = _scope(q, ctx)
    if not ok:
        return None
    locked = ctx["m"][~ctx["m"]["achieved"] & ctx["m"]["rarity_pct"].notna()]
    if appid is not None:
        locked = locked[locked["appid"] == appid]
    if locked.empty:
        return None
    rows = locked.sort_values("rarity_pct", ascending=False).head(5)
    lines = [f"- **{r.display_name}** ({_p(r.rarity_pct)} of players have it) — {_g(ctx, r.appid)}"
             for r in rows.itertuples(index=False)]
    where = f" in **{_g(ctx, appid)}**" if appid is not None else ""
    return f"Easiest achievements you're still missing{where}:\n" + "\n".join(lines)


def _most_played(q, ctx):
    games = ctx["games"]
    if games.empty or "playtime" not in games.columns:
        return None
    r = games.loc[games["playtime"].idxmax()]
    return (f"Your most played game is **{r['name']}** — about "
            f"**{int(round(float(r['playtime']) / 60))} hours**.")


def _game_most_ach(q, ctx):
    per = ctx["per"]
    if per.empty:
        return None
    r = per.loc[per["total"].idxmax()]
    return (f"**{_g(ctx, r['appid'])}** has the most achievements: "
            f"**{int(r['total'])}** (you've unlocked {int(r['unlocked'])}).")


def _game_most_unlocked(q, ctx):
    per = ctx["per"]
    if per.empty:
        return None
    r = per.loc[per["unlocked"].idxmax()]
    return (f"You've unlocked the most achievements in **{_g(ctx, r['appid'])}**: "
            f"**{int(r['unlocked'])}** of {int(r['total'])} ({_p(r['pct'])}).")


def _no_ach_games(q, ctx):
    games, m = ctx["games"], ctx["m"]
    with_ach = set(m["appid"].unique())
    none = [str(n) for a, n in zip(games["appid"], games["name"]) if a not in with_ach]
    if not none:
        return "Every game you own has achievements."
    return (f"**{len(none)}** of your games have no achievements: " + ", ".join(none) + ".")


def _started_count(q, ctx):
    per, games = ctx["per"], ctx["games"]
    started = int((per["unlocked"] > 0).sum())
    return (f"You've made progress (≥1 achievement) in **{started}** of your "
            f"**{len(games)}** games.")


def _stalled(q, ctx):
    per = ctx["per"]
    rows = per[(per["unlocked"] > 0) & (per["pct"] < 10)].sort_values("pct").head(5)
    if rows.empty:
        return "No stalled games — everything you've started is past 10% completion."
    lines = [f"- **{_g(ctx, r.appid)}** — {_p(r.pct)} ({int(r.total - r.unlocked)} left)"
             for r in rows.itertuples(index=False)]
    return "Games you started but stalled on (<10%):\n" + "\n".join(lines)


def _totals(q, ctx):
    appid, _, ok = _scope(q, ctx)
    if not ok or appid is not None:
        return None  # game-scoped totals are handled by the game shapes
    m = ctx["m"]
    t, u = int(len(m)), int(m["achieved"].sum())
    if not t:
        return None
    return (f"You've unlocked **{u}** of **{t}** achievements across your library — "
            f"**{_p(u / t * 100)}** overall.")


# Game-scoped shapes: matched on the question WITH the game name removed, so a
# title like "Left 4 Dead 2" can't accidentally trigger the "left/remaining"
# wording check.

def _game_remaining(q, ctx):
    hit = _match_owned_game(q, ctx["games"])
    if not hit:
        return None
    name, appid = hit
    qq = q.lower().replace(name.lower(), " ")
    if not re.search(r"\b(left|remaining|to\s+go|away\s+from|still\s+need)\b", qq):
        return None
    row = ctx["per"][ctx["per"]["appid"] == appid]
    if row.empty:
        return None
    r = row.iloc[0]
    rem = int(r["total"] - r["unlocked"])
    return (f"**{rem}** achievements left in **{name}** — you're at "
            f"{int(r['unlocked'])}/{int(r['total'])} ({_p(r['pct'])}).")


def _game_pct(q, ctx):
    hit = _match_owned_game(q, ctx["games"])
    if not hit:
        return None
    name, appid = hit
    qq = q.lower().replace(name.lower(), " ")
    if not re.search(r"%|percent|completion|progress|how\s+far|unlocked", qq):
        return None
    row = ctx["per"][ctx["per"]["appid"] == appid]
    if row.empty:
        return None
    r = row.iloc[0]
    return (f"**{name}**: {int(r['unlocked'])}/{int(r['total'])} achievements — "
            f"**{_p(r['pct'])}** complete.")


# ── shape table (order = priority; game-scoped and specific before generic) ──

_SHAPES: list[tuple[str, re.Pattern, Optional[re.Pattern], Callable]] = [
    ("game_remaining", re.compile(r"\b(left|remaining|to\s+go|away\s+from|still\s+need)\b", re.I),
     None, _game_remaining),
    ("game_pct", re.compile(r"%|\bpercent(age)?\b|\bcompletion\b|\bprogress\b|\bhow\s+far\b", re.I),
     None, _game_pct),
    ("rarest", re.compile(r"\brarest\b", re.I),
     re.compile(r"\b(locked|haven'?t|not\s+unlocked|missing|still\s+(get|pull|unlock)|to\s+unlock|rarer\s+than)\b", re.I),
     _rarest),
    ("most_common", re.compile(r"\bmost\s+common\b", re.I), None, _most_common),
    ("recent", re.compile(r"\b(most\s+recent|last|latest)\b.{0,30}\b(achievement|unlock|pull)", re.I),
     None, _recent),
    ("hidden", re.compile(r"\bhidden\b.{0,40}\bachievements?\b|\bachievements?\b.{0,40}\bhidden\b", re.I),
     None, _hidden),
    ("perfect", re.compile(r"\bperfect(ed)?\s+games?\b|\bfully\s+complet\w+|\b100%\s*(complete|done|games?)\b|\bgames?\s+(at|with)\s+100%", re.I),
     None, _perfect),
    ("closest", re.compile(r"\bclosest\b.{0,30}\b(100|complet|finish)|\balmost\s+(done|complete|finished)\b", re.I),
     None, _closest),
    ("next_target", re.compile(r"what\s+should\s+i\s+(play|finish)|easiest\s+game\s+to\s+(100|complete|finish)|fastest\s+(game\s+to\s+)?100", re.I),
     None, _next_target),
    ("easy_wins", re.compile(r"\b(easy|easiest|quick)\b.{0,50}\b(wins?|achievements?|unlocks?|grab|get)\b", re.I),
     None, _easy_wins),
    ("most_played", re.compile(r"\bmost\s+played\b|\bplayed\s+the\s+most\b|\bmost\s+(hours|playtime)\b", re.I),
     None, _most_played),
    ("game_most_ach", re.compile(r"game\s+has\s+the\s+most\s+achievements|most\s+achievements\b.{0,20}\bgame\b", re.I),
     re.compile(r"\bunlock", re.I), _game_most_ach),
    ("game_most_unlocked", re.compile(r"\bunlocked\s+the\s+most\b|most\s+achievements\s+unlocked", re.I),
     None, _game_most_unlocked),
    ("no_ach_games", re.compile(r"\b(no|without|zero)\s+achievements?\b", re.I),
     None, _no_ach_games),
    ("started_count", re.compile(r"how\s+many\s+(of\s+my\s+)?games\b.{0,40}\b(progress|started|touched)", re.I),
     None, _started_count),
    ("stalled", re.compile(r"\b(stalled|abandoned)\b", re.I), None, _stalled),
    ("totals", re.compile(r"how\s+many\s+achievements?\s+(have|did|do)\s+i|\b(overall|total)\s+(completion|progress|achievements)\b|what('?s|\s+is)\s+my\s+(overall\s+)?completion", re.I),
     None, _totals),
]


def fast_answer(question: str, steam_id: Optional[str] = None) -> Optional[dict]:
    """Answer a common question shape straight from the snapshot, or return None
    to fall through to the full agent. Never raises."""
    q = (question or "").strip()
    # Compound/ambiguous questions go to the agent: long, multi-part ("and"),
    # multiple questions, or anything on the fall-through list.
    if (not q or len(q) > 120 or q.count("?") > 1
            or re.search(r"\band\b", q, re.IGNORECASE) or _FALLTHROUGH.search(q)):
        return None
    try:
        frames = load_frames(steam_id or STEAM_ID)
        if not frames or all(df.empty for df in frames.values()):
            return None
        ctx = _build_ctx(frames)
        for key, include, exclude, fn in _SHAPES:
            if include.search(q) and not (exclude and exclude.search(q)):
                ans = fn(q, ctx)
                if ans:
                    return {"answer": ans, "route": f"fastpath:{key}",
                            "fastpath": True, "done": True}
                # Matched wording but declined (wrong scope / no data) — let the
                # remaining, more generic shapes try before giving up.
                continue
    except Exception:
        return None  # any hiccup → the agent path is always the safe fallback
    return None
