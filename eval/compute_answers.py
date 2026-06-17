"""Compute ground-truth answers from the frozen snapshot.

Run this any time you want to re-verify the expected values in
golden_questions.json (e.g. after rebuilding the snapshot).

    python -m eval.compute_answers
"""
import pandas as pd
from data_layer.snapshot import load_frames

f = load_frames()
games   = f["games"]
ach     = f["achievements"]
unlocks = f["player_unlocks"]

# Reusable joins
full = unlocks.merge(ach, on=["appid", "api_name"]).merge(games[["appid", "name"]], on="appid")
per_game = (
    unlocks.groupby("appid")
           .agg(total=("achieved", "count"), done=("achieved", "sum"))
           .reset_index()
)
per_game["pct"] = (per_game["done"] / per_game["total"] * 100).round(2)
per_game = per_game[per_game["total"] > 0].merge(games[["appid", "name"]], on="appid")
unlocked = full[(full["achieved"] == True) & (full["unlock_time"] > 0)].copy()
unlocked["unlock_dt"] = pd.to_datetime(unlocked["unlock_time"], unit="s")

# --- Q01: total achievements unlocked ---
print("Q01 total unlocked:", int(unlocks["achieved"].sum()))

# --- Q02: game closest to 100% (not yet complete) ---
not_complete = per_game[per_game["pct"] < 100].sort_values("pct", ascending=False)
print("Q02 closest to 100%:")
print(not_complete[["name", "pct", "done", "total"]].head(3).to_string())

# --- Q03: games at exactly 100% ---
complete = per_game[per_game["pct"] == 100]
print("Q03 completed games:")
print(complete[["name", "pct"]].to_string())

# --- Q04: rarest achievement unlocked ---
unlocked_full = full[full["achieved"] == True]
print("Q04 rarest unlocked:")
print(unlocked_full.nsmallest(1, "rarity_pct")[["display_name", "rarity_pct", "name"]].to_string())

# --- Q05: most common achievement unlocked ---
print("Q05 most common unlocked:")
print(unlocked_full.nlargest(1, "rarity_pct")[["display_name", "rarity_pct", "name"]].to_string())

# --- Q06: most played game ---
print("Q06 most played (minutes):")
print(games.nlargest(1, "playtime")[["name", "playtime"]].to_string())

# --- Q07: games with no achievements ---
no_ach = games[~games["appid"].isin(ach["appid"].unique())]
print("Q07 games with no achievements:", len(no_ach))
print(no_ach["name"].tolist())

# --- Q08: total hidden achievements ---
print("Q08 total hidden achievements:", int(ach["hidden"].sum()))

# --- Q09: hidden achievements unlocked ---
hidden_unlocked = full[(full["hidden"] == True) & (full["achieved"] == True)]
print("Q09 hidden achievements unlocked:", len(hidden_unlocked))

# --- Q10: game with most achievements ---
print("Q10 game with most achievements:")
most_ach = (
    ach.groupby("appid").size()
       .reset_index(name="count")
       .merge(games[["appid", "name"]], on="appid")
       .nlargest(1, "count")
)
print(most_ach[["name", "count"]].to_string())

# --- Q11: L4D2 completion (appid 550) ---
print("Q11 Left 4 Dead 2 completion:")
print(per_game[per_game["appid"] == 550][["name", "pct", "done", "total"]].to_string())

# --- Q12: games with at least 1 unlock ---
print("Q12 games with at least 1 unlock:", len(per_game[per_game["done"] > 0]))

# --- Q13: average completion % across all games with achievements ---
print("Q13 avg completion pct:", round(per_game["pct"].mean(), 2))

# --- Q14: most achievements unlocked in a single game ---
print("Q14 most unlocked in one game:")
print(per_game.nlargest(1, "done")[["name", "done", "total", "pct"]].to_string())

# --- Q15: most recent achievement unlocked ---
print("Q15 most recent unlock:")
print(unlocked.nlargest(1, "unlock_time")[["display_name", "name", "unlock_dt"]].to_string())

# --- Q16: unlocked achievements rarer than 5% ---
rare = full[(full["achieved"] == True) & (full["rarity_pct"] < 5)]
print("Q16 unlocked achievements rarer than 5%:", len(rare))

# --- Q17: Rocket League completion ---
print("Q17 Rocket League completion:")
print(per_game[per_game["name"] == "Rocket League"][["name", "pct", "done", "total"]].to_string())

# --- Q18: games with progress, lowest completion ---
print("Q18 lowest completion (with any progress):")
print(per_game[per_game["done"] > 0].nsmallest(3, "pct")[["name", "pct", "done", "total"]].to_string())

# --- Q19: Assetto Corsa total vs unlocked ---
print("Q19 Assetto Corsa:")
print(per_game[per_game["name"] == "Assetto Corsa"][["name", "pct", "done", "total"]].to_string())

# --- Q20: top 3 rarest unlocked ---
print("Q20 top 3 rarest unlocked:")
print(unlocked_full.nsmallest(3, "rarity_pct")[["display_name", "rarity_pct", "name"]].to_string())
