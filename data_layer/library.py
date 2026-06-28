"""Build the "Trophy Case" library view (Step 17, Phase A).

A read-only transform over the user's snapshot into the shape the trophy-case UI
consumes — profile stats, every achievement as a card, per-game breakdowns, and a
few precomputed "curator" highlights (rarest / quick wins / closest / stalled /
beatable). Pure pandas over load_frames + the raw schema (for icons); no agent,
no network. Returns plain JSON-serializable types.
"""
import pandas as pd

from .snapshot import load_frames, load_schemas

_RAREST_N = 6
_QUICK_N = 6
_STALLED_N = 6
_BEATABLE_N = 8


def _icon_map(steam_id: str) -> dict:
    """{(appid, api_name): icon_url} from the raw schema."""
    out = {}
    for appid_str, sch in load_schemas(steam_id).items():
        try:
            appid = int(appid_str)
        except (TypeError, ValueError):
            continue
        for a in sch.get("game", {}).get("availableGameStats", {}).get("achievements", []):
            out[(appid, a.get("name", ""))] = a.get("icon", "")
    return out


def _pct(v) -> float | None:
    return round(float(v), 1) if pd.notna(v) else None


# Prototype's rarity thresholds (must match the UI's tierOf exactly).
def _tier(pct) -> str | None:
    if pct is None or pd.isna(pct):
        return None
    if pct < 5:
        return "ultra"
    if pct < 10:
        return "rare"
    if pct < 30:
        return "uncommon"
    return "common"


def build_library(steam_id: str) -> dict:
    frames = load_frames(steam_id)
    games, ach, pu = frames["games"], frames["achievements"], frames["player_unlocks"]
    icons = _icon_map(steam_id)
    gname = dict(zip(games["appid"], games["name"]))
    gplay = dict(zip(games["appid"], games.get("playtime", pd.Series(dtype="int64"))))

    # Join schema achievements with the player's unlock status.
    merged = ach.merge(
        pu[["appid", "api_name", "achieved", "unlock_time"]],
        on=["appid", "api_name"], how="left",
    )
    merged["achieved"] = merged["achieved"].fillna(False).astype(bool)
    merged["unlock_time"] = merged["unlock_time"].fillna(0).astype("int64")

    def _ach(row) -> dict:
        return {
            "name": row.display_name or row.api_name,
            "desc": row.description or "",  # show real descriptions (completion tool — spoilers welcome)
            "game": gname.get(row.appid, str(row.appid)),
            "icon": icons.get((row.appid, row.api_name), ""),
            "pct": _pct(row.rarity_pct),
            "achieved": bool(row.achieved),
            "t": int(row.unlock_time) if row.achieved and row.unlock_time else None,
            "hidden": bool(row.hidden),
        }

    cards = [_ach(r) for r in merged.itertuples(index=False)]

    # Per-game breakdown.
    games_out = []
    for appid, grp in merged.groupby("appid"):
        total = int(len(grp))
        if total == 0:
            continue
        unlocked = int(grp["achieved"].sum())
        achs = [_ach(r) for r in grp.itertuples(index=False)]
        avg_global = grp["rarity_pct"].mean()
        games_out.append({
            "game": gname.get(appid, str(appid)),
            "app": int(appid),
            "total": total,
            "unlocked": unlocked,
            "pct": round(unlocked / total * 100, 1),
            "avg": _pct(avg_global) or 0,
            "play": int(round((gplay.get(appid, 0) or 0) / 60)),  # minutes → hours
            "achievements": achs,
        })
    games_out.sort(key=lambda g: (g["pct"], g["unlocked"]), reverse=True)

    # Profile headline.
    games_with_ach = len(games_out)
    started = sum(1 for g in games_out if g["unlocked"] > 0)
    perfect = sum(1 for g in games_out if g["unlocked"] == g["total"])
    total_unlocked = int(merged["achieved"].sum())
    total_ach = int(len(merged))
    profile = {
        "name": "",  # filled by the API from the player summary
        "avatar": "",
        "gamesTotal": int(len(games)),
        "gamesWithAch": games_with_ach,
        "started": started,
        "perfect": perfect,
        "unlocked": total_unlocked,
        "total": total_ach,
        "overall": round(total_unlocked / total_ach * 100) if total_ach else 0,
    }

    curator = _curator(merged, games_out, gname)

    # Rarity mix of UNLOCKED achievements (collector profile bars).
    unlocked_cards = [c for c in cards if c["achieved"]]
    mix = {"common": 0, "uncommon": 0, "rare": 0, "ultra": 0}
    for c in unlocked_cards:
        t = _tier(c["pct"])
        if t:
            mix[t] += 1
    # Featured-flex: rarest unlocked, full card objects (rotated in the UI).
    flex = sorted((c for c in unlocked_cards if c["pct"] is not None),
                  key=lambda c: c["pct"])[:6]
    library = [g["game"] for g in games_out]

    return {"profile": profile, "cards": cards, "games": games_out,
            "curator": curator, "mix": mix, "flex": flex, "library": library}


def _curator(merged: pd.DataFrame, games_out: list[dict], gname: dict) -> dict:
    def tag(df):
        return [
            {"name": r.display_name or r.api_name, "game": gname.get(r.appid, str(r.appid)),
             "pct": _pct(r.rarity_pct)}
            for r in df.itertuples(index=False)
        ]

    have_pct = merged[merged["rarity_pct"].notna()]
    # Rarest unlocked = lowest global %.
    rarest = tag(have_pct[have_pct["achieved"]].nsmallest(_RAREST_N, "rarity_pct"))
    # Quick wins = locked achievements most players already have.
    quick = tag(have_pct[~have_pct["achieved"]].nlargest(_QUICK_N, "rarity_pct"))

    started = [g for g in games_out if 0 < g["pct"] < 100]
    closest = None
    if started:
        c = max(started, key=lambda g: g["pct"])
        closest = {"game": c["game"], "pct": c["pct"], "remaining": c["total"] - c["unlocked"]}
    stalled = [{"game": g["game"], "pct": g["pct"]}
               for g in sorted(started, key=lambda g: g["unlocked"], reverse=True)[:_STALLED_N]]
    # Beatable = easiest games (highest avg global completion) you haven't finished.
    beatable = [
        {"game": g["game"], "total": g["total"], "userPct": g["pct"], "avg": g["avg"]}
        for g in sorted((g for g in games_out if g["pct"] < 100 and g["total"] >= 5),
                        key=lambda g: g["avg"], reverse=True)[:_BEATABLE_N]
    ]
    return {"rarest": rarest, "quick": quick, "closest": closest,
            "stalled": stalled, "beatable": beatable}
