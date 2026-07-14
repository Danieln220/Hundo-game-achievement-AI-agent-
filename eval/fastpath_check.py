"""Deterministic fast-path check (Step 19.3) — NO LLM calls, runs in seconds.

Asserts, against the local snapshot:
  1. HITS  — each shape fires on several phrasings and its numbers match the
             frames (expected values are computed here, independently).
  2. MISSES — compound / follow-up / guide / unowned-scope questions must
             return None (they belong to the full agent).
  3. SPEED — a fast-path answer completes in well under a second of compute.

Usage (from repo root):  python -m eval.fastpath_check
"""
import re
import sys
import time

from agent.fastpath import fast_answer
from config import STEAM_ID
from data_layer.snapshot import load_frames

ok_count = fail_count = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global ok_count, fail_count
    status = "PASS" if cond else "FAIL"
    ok_count += int(cond)
    fail_count += int(not cond)
    print(f"{status}  {label}" + (f"  [{detail}]" if detail and not cond else ""))


def main() -> None:
    frames = load_frames(STEAM_ID)
    games, ach, pu = frames["games"], frames["achievements"], frames["player_unlocks"]
    m = ach.merge(pu[["appid", "api_name", "achieved", "unlock_time"]],
                  on=["appid", "api_name"], how="left")
    m["achieved"] = m["achieved"].fillna(False).astype(bool)
    gname = dict(zip(games["appid"], games["name"].astype(str)))

    # Independent expected values.
    total_unlocked = int(m["achieved"].sum())
    have = m[m["achieved"] & m["rarity_pct"].notna()]
    rarest_name = str(have.loc[have["rarity_pct"].idxmin()]["display_name"])
    most_played = str(games.loc[games["playtime"].idxmax()]["name"])
    per = m.groupby("appid").agg(total=("achieved", "size"),
                                 unlocked=("achieved", "sum")).reset_index()
    per["pct"] = per["unlocked"] / per["total"] * 100
    perfect = [gname[a] for a in per[(per["unlocked"] >= per["total"])]["appid"]]
    started = per[(per["unlocked"] > 0) & (per["pct"] < 100)].copy()
    started["remaining"] = started["total"] - started["unlocked"]
    focus_game = gname[started.sort_values("remaining").iloc[0]["appid"]]
    focus_left = int(started.sort_values("remaining").iloc[0]["remaining"])
    closest_game = gname[started.sort_values("pct", ascending=False).iloc[0]["appid"]]
    hidden_total = int(m["hidden"].sum())
    u = m[m["achieved"] & (m["unlock_time"] > 0)]
    last_unlock = str(u.loc[u["unlock_time"].idxmax()]["display_name"])
    recent_games = [gname[a] for a in
                    u.sort_values("unlock_time", ascending=False)["appid"]
                    .drop_duplicates().head(3)]

    def hits(question: str, shape: str, *must_contain: str) -> None:
        r = fast_answer(question)
        got = (r or {}).get("route", "None")
        ans = (r or {}).get("answer", "")
        good = r is not None and got == f"fastpath:{shape}" and all(
            s.lower() in ans.lower() for s in must_contain)
        check(f"HIT  {question!r} -> {shape}", good, f"got route={got} answer={ans[:90]}")

    def misses(question: str) -> None:
        r = fast_answer(question)
        check(f"MISS {question!r} -> agent", r is None,
              f"got {r and r.get('route')}: {(r or {}).get('answer', '')[:80]}")

    # ── hits ──────────────────────────────────────────────────────────────────
    hits("What's my rarest achievement?", "rarest", rarest_name)
    hits("rarest achievement I have unlocked", "rarest", rarest_name)
    hits("top 3 rarest achievements", "rarest", rarest_name)
    hits("How many achievements have I unlocked?", "totals", str(total_unlocked))
    hits("what is my overall completion?", "totals", str(total_unlocked))
    hits("Which game have I played the most?", "most_played", most_played)
    hits("which games have I fully completed?", "perfect", *(perfect[:1] or ["closest"]))
    hits("what games am I closest to finishing at 100%?", "closest", closest_game)
    hits("what should I play next?", "next_target", focus_game, str(focus_left))
    hits("easiest achievements I haven't unlocked", "easy_wins")
    hits("easy wins in Rocket League", "easy_wins", "Rocket League")
    hits("what is my completion percentage in Rocket League?", "game_pct", "Rocket League")
    hits("What percentage of Left 4 Dead 2 achievements have I unlocked?", "game_pct",
         "Left 4 Dead 2")
    hits("how many achievements are left in Company of Heroes 3?", "game_remaining",
         "Company of Heroes 3", "6")
    hits("how many hidden achievements are there in my games?", "hidden", str(hidden_total))
    hits("what was my latest unlock, which achievement?", "recent", last_unlock)
    hits("Which three of my games have the most recent achievement unlock?", "recent",
         *recent_games)
    hits("what were my last 5 unlocks?", "recent", last_unlock)
    hits("three rarest achievements", "rarest", rarest_name)
    hits("which game has the most achievements?", "game_most_ach")
    hits("my stalled games?", "stalled")
    hits("which of my games have no achievements?", "no_ach_games")

    # ── must fall through to the agent ────────────────────────────────────────
    misses("Build me a roadmap to 100% Rocket League")
    misses("how long to 100% Hollow Knight?")
    misses("audit my profile")
    misses("give me a full report")
    misses("how do I unlock Friendly in Rocket League?")
    misses("what about Rocket League?")                    # follow-up
    misses("when did I unlock it?")                        # pronoun, needs history
    misses("compare me with my friends")
    misses("How many achievements does Assetto Corsa have and how many have I unlocked?")
    misses("what's my rarest achievement in Elden Ring?")  # unowned scope → agent
    misses("hi")
    misses("why do I keep abandoning RPGs?")

    # ── speed ────────────────────────────────────────────────────────────────
    t0 = time.perf_counter()
    fast_answer("What's my rarest achievement?")
    dt = time.perf_counter() - t0
    check(f"SPEED fast_answer in {dt * 1000:.0f}ms (< 1000ms)", dt < 1.0)

    print(f"\n  {ok_count} passed, {fail_count} failed")
    sys.exit(1 if fail_count else 0)


if __name__ == "__main__":
    main()
