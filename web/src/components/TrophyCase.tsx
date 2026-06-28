import { useEffect, useMemo, useRef, useState } from "react";
import { getLibrary, getPopular } from "../api";
import type { Card, LibGame, LibraryData, SessionResult } from "../types";
import { C, tierOf, tierColor, pctLabel, HOLO, STEAM_HEADER, onImgError } from "../tcTheme";
import AchievementCard from "./AchievementCard";
import CuratorDrawer from "./CuratorDrawer";
import RoadmapView from "./RoadmapView";

type FilterKey = "all" | "ultra" | "rare" | "uncommon" | "common";
type ShowKey = "all" | "unlocked" | "locked";
type SortKey = "rare" | "common" | "recent";

const FONT_MONO = "'JetBrains Mono',monospace";
const FONT_HEAD = "'Chakra Petch',sans-serif";

// Curated "popular to beat" list (lifted from the prototype). Ownership is overlaid
// from the user's library; everything else is editorial.
const BEAT_GAMES = [
  { name: "Portal 2", appid: 620, hours: 13, diff: 1, note: "Tight, fair puzzles — the friendliest full clear in gaming." },
  { name: "It Takes Two", appid: 1426210, hours: 15, diff: 1, note: "Co-op story; nearly everything unlocks just by finishing." },
  { name: "Vampire Survivors", appid: 1794680, hours: 70, diff: 2, note: "Unlocks cascade into each other — a relaxed checklist." },
  { name: "Subnautica", appid: 264710, hours: 45, diff: 2, note: "Exploration-driven, no skill gates, very few missables." },
  { name: "Stardew Valley", appid: 413150, hours: 150, diff: 3, note: "Long but gentle — Perfection is a cozy marathon." },
  { name: "Hades", appid: 1145360, hours: 95, diff: 3, note: "Story-gated; the runs naturally carry you to most of it." },
  { name: "DOOM Eternal", appid: 782330, hours: 45, diff: 4, note: "Combat mastery plus a Master Levels grind raise the bar." },
  { name: "Hollow Knight", appid: 367520, hours: 62, diff: 4, note: "Pantheon bosses make the final stretch genuinely tough." },
  { name: "Elden Ring", appid: 1245620, hours: 130, diff: 4, note: "Forgiving on missables — time is the real cost here." },
  { name: "Cuphead", appid: 268910, hours: 25, diff: 5, note: "Boss-rush precision; the S-ranks are brutal." },
  { name: "Celeste", appid: 504230, hours: 38, diff: 5, note: "C-sides and golden strawberries are a true test of nerve." },
];
const DIFF_COL: Record<number, string> = { 1: C.uncommon, 2: C.uc, 3: C.gold, 4: C.rare, 5: C.ub };
const DIFF_LAB: Record<number, string> = { 1: "Breezy", 2: "Easy", 3: "Moderate", 4: "Hard", 5: "Brutal" };

const panel: React.CSSProperties = {
  background: "linear-gradient(180deg,#12151f,#0e111a)", border: `1px solid ${C.edge}`,
  borderRadius: 16, boxShadow: "inset 0 1px 0 rgba(255,255,255,.03)",
};
const sectionLabel: React.CSSProperties = {
  fontFamily: FONT_HEAD, fontWeight: 600, textTransform: "uppercase", letterSpacing: "1px",
  fontSize: 12, color: C.inkDim, marginRight: 4,
};
const hr: React.CSSProperties = { flex: 1, height: 1, background: C.edge, minWidth: 20 };

function chipBtn(active: boolean, color: string): React.CSSProperties {
  return {
    display: "flex", alignItems: "center", gap: 5, padding: "5px 11px", borderRadius: 999, cursor: "pointer",
    fontFamily: FONT_HEAD, fontSize: 12.5, fontWeight: 600, letterSpacing: ".2px",
    background: active ? "linear-gradient(180deg,#1a1f2e,#12151f)" : "transparent",
    color: active ? color : C.inkDim, border: `1px solid ${active ? color : C.edge}`, transition: "all .15s",
  };
}

export default function TrophyCase({ session, onSignOut }: { session: SessionResult; onSignOut: () => void }) {
  const [lib, setLib] = useState<LibraryData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<FilterKey>("all");
  const [show, setShow] = useState<ShowKey>("all");
  const [search, setSearch] = useState("");
  const [sort, setSort] = useState<SortKey>("rare");
  const [game, setGame] = useState("all");
  const [limit, setLimit] = useState(30);
  const [gameSearch, setGameSearch] = useState("");
  const [selected, setSelected] = useState<Card | null>(null);
  const [gameView, setGameView] = useState<LibGame | null>(null);
  const [curatorOpen, setCuratorOpen] = useState(false);
  const [inject, setInject] = useState<{ q: string; nonce: number }>({ q: "", nonce: 0 });
  const [roadmapOpen, setRoadmapOpen] = useState(false);
  const [roadmapGame, setRoadmapGame] = useState("");
  const [popular, setPopular] = useState<{ appid: number; name: string }[]>([]);
  const gamesRef = useRef<HTMLDivElement>(null);
  const beatRef = useRef<HTMLDivElement>(null);

  useEffect(() => { getLibrary(session.steam_id).then(setLib).catch((e) => setError((e as Error).message)); }, [session.steam_id]);
  useEffect(() => { getPopular(session.steam_id).then((r) => setPopular(r.games)).catch(() => {}); }, [session.steam_id]);
  useEffect(() => setLimit(30), [filter, show, search, sort, game]);

  function askCurator(q: string) { setInject((i) => ({ q, nonce: i.nonce + 1 })); setCuratorOpen(true); setSelected(null); setGameView(null); }
  function openRoadmap(g = "") { setRoadmapGame(g); setRoadmapOpen(true); setSelected(null); setGameView(null); }

  const filtered = useMemo(() => {
    if (!lib) return [];
    const q = search.trim().toLowerCase();
    let out = lib.cards.filter((a) =>
      (filter === "all" || tierOf(a.pct) === filter) &&
      (show === "all" || (show === "unlocked" ? a.achieved : !a.achieved)) &&
      (!q || a.name.toLowerCase().includes(q) || a.game.toLowerCase().includes(q)) &&
      (game === "all" || a.game === game));
    out = [...out];
    if (sort === "rare") out.sort((a, b) => (a.pct ?? 101) - (b.pct ?? 101));
    else if (sort === "common") out.sort((a, b) => (b.pct ?? -1) - (a.pct ?? -1));
    else out.sort((a, b) => (b.achieved ? (b.t ?? 0) : -1) - (a.achieved ? (a.t ?? 0) : -1));
    return out;
  }, [lib, filter, show, search, sort, game]);

  if (error) return <div style={{ textAlign: "center", color: "#ef6a6a", padding: 60, fontFamily: FONT_MONO }}>Couldn't load your trophy case: {error}</div>;
  if (!lib) return <div style={{ textAlign: "center", color: C.inkDim, padding: 80, fontFamily: FONT_MONO }}>Cataloguing your case…</div>;

  const p = lib.profile;
  const rarest = lib.curator.rarest[0];
  const circ = 2 * Math.PI * 60;
  const shown = filtered.slice(0, limit);
  const gameNames = [...new Set(lib.cards.map((a) => a.game))].sort((a, b) => a.localeCompare(b));
  const mixMax = Math.max(lib.mix.common, lib.mix.uncommon, lib.mix.rare, lib.mix.ultra, 1);
  const mixTotal = lib.mix.common + lib.mix.uncommon + lib.mix.rare + lib.mix.ultra;
  const elite = mixTotal ? Math.round((lib.mix.rare + lib.mix.ultra) / mixTotal * 100) : 0;
  const owned = new Set(lib.library.map((n) => n.toLowerCase()));
  const myGames = (gameSearch.trim()
    ? lib.games.filter((g) => g.game.toLowerCase().includes(gameSearch.trim().toLowerCase()))
    : lib.games).slice().sort((a, b) => b.play - a.play);

  if (roadmapOpen) {
    return (
      <div style={{ maxWidth: 1320, margin: "0 auto", padding: "0 32px 110px" }}>
        <RoadmapView steamId={session.steam_id} library={lib.games} initialGame={roadmapGame || undefined} onClose={() => setRoadmapOpen(false)} />
      </div>
    );
  }

  const tileStat: React.CSSProperties = { background: "linear-gradient(180deg,#1a1f2e,#12151f)", border: `1px solid ${C.edge}`, borderRadius: 12, padding: 13, textAlign: "center" };
  const tileNum: React.CSSProperties = { fontFamily: FONT_MONO, fontSize: 22, fontWeight: 700, color: C.gold };
  const tileCap: React.CSSProperties = { fontSize: 10, color: C.inkDim, textTransform: "uppercase", letterSpacing: ".6px", marginTop: 2 };

  return (
    <div style={{ maxWidth: 1320, margin: "0 auto", padding: "0 32px 110px" }}>
      {/* HEADER */}
      <header style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "20px 2px 16px", borderBottom: `1px solid ${C.edge}` }}>
        <div style={{ display: "flex", alignItems: "center", gap: 13 }}>
          <span style={{ width: 42, height: 42, display: "grid", placeItems: "center", background: "linear-gradient(180deg,#1a1f2e,#12151f)", border: `1px solid ${C.edge}`, borderRadius: 11, position: "relative" }}>
            <svg width="24" height="24" viewBox="0 0 24 24" fill="none">
              <circle cx="12" cy="12" r="9" stroke={C.edge} strokeWidth="3" />
              <circle cx="12" cy="12" r="9" stroke={C.gold} strokeWidth="3" strokeLinecap="round" strokeDasharray="56.5" strokeDashoffset="15" transform="rotate(-90 12 12)" />
            </svg>
            <span style={{ position: "absolute", fontFamily: FONT_HEAD, fontWeight: 700, fontSize: 11, color: C.gold }}>H</span>
          </span>
          <div>
            <h1 style={{ margin: 0, fontFamily: FONT_HEAD, fontSize: 21, fontWeight: 700 }}>Hundo</h1>
            <span style={{ display: "block", fontFamily: FONT_MONO, fontSize: 11, letterSpacing: "1.6px", textTransform: "uppercase", color: C.gold, fontWeight: 600 }}>The Trophy Case</span>
          </div>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 11 }}>
          <button onClick={onSignOut} style={{ background: "transparent", color: C.inkDim, border: `1px solid ${C.edge}`, borderRadius: 9, padding: "7px 12px", cursor: "pointer", fontSize: 12.5, fontFamily: FONT_HEAD }}>Sign out</button>
          <div style={{ textAlign: "right" }}>
            <div style={{ fontFamily: FONT_HEAD, fontSize: 15, fontWeight: 600 }}>{p.name}</div>
            <div style={{ fontFamily: FONT_MONO, fontSize: 11, color: C.inkDim }}>{p.gamesTotal} games · {p.unlocked.toLocaleString()} unlocks</div>
          </div>
          {p.avatar && <img src={p.avatar} alt="" style={{ width: 46, height: 46, borderRadius: 11, objectFit: "cover", border: `1px solid ${C.edgeLit}`, boxShadow: `0 0 0 3px ${C.void}, 0 0 0 4px rgba(232,179,57,.15)` }} />}
        </div>
      </header>

      {/* SHORTCUTS */}
      <div style={{ display: "flex", gap: 8, marginTop: 13, flexWrap: "wrap" }}>
        <button onClick={() => openRoadmap()} style={{ display: "flex", alignItems: "center", gap: 8, padding: "7px 14px", borderRadius: 999, cursor: "pointer", fontFamily: FONT_HEAD, fontSize: 13, fontWeight: 700, background: `linear-gradient(180deg,${C.gold},#c9991f)`, color: "#1a1303", border: "none" }}>
          🗺️ Build a roadmap
        </button>
        {[["Your games", "#8b7bf0", gamesRef], ["Easiest to beat", C.gold, beatRef]].map(([label, dot, ref], i) => (
          <button key={i} onClick={() => (ref as React.RefObject<HTMLDivElement>).current?.scrollIntoView({ behavior: "smooth", block: "start" })}
            style={{ display: "flex", alignItems: "center", gap: 8, padding: "7px 13px", borderRadius: 999, cursor: "pointer", fontFamily: FONT_HEAD, fontSize: 13, fontWeight: 600, background: "linear-gradient(180deg,#12151f,#0e111a)", color: "#c2c9d6", border: `1px solid ${C.edge}` }}>
            <span style={{ width: 7, height: 7, borderRadius: "50%", background: dot as string }} /> {label as string}
          </button>
        ))}
      </div>

      {/* HERO */}
      <section style={{ ...panel, display: "flex", gap: 26, alignItems: "center", flexWrap: "wrap", padding: "26px 18px", marginTop: 18, position: "relative", overflow: "hidden" }}>
        <div style={{ position: "absolute", inset: "0 0 auto 0", height: 2, background: `linear-gradient(90deg,transparent,${C.gold},transparent)`, opacity: 0.4 }} />
        <div style={{ position: "relative", flex: "none", display: "grid", placeItems: "center" }}>
          <svg width="148" height="148" viewBox="0 0 148 148">
            <circle cx="74" cy="74" r="60" fill="none" stroke={C.case2} strokeWidth="12" />
            <circle cx="74" cy="74" r="60" fill="none" stroke="url(#ring)" strokeWidth="12" strokeLinecap="round" strokeDasharray={circ.toFixed(1)} strokeDashoffset={(circ * (1 - p.overall / 100)).toFixed(1)} transform="rotate(-90 74 74)" />
            <defs><linearGradient id="ring" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stopColor={C.goldLo} /><stop offset="1" stopColor={C.gold} /></linearGradient></defs>
          </svg>
          <div style={{ position: "absolute", inset: 0, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", lineHeight: 1 }}>
            <div style={{ fontFamily: FONT_MONO, fontWeight: 700, fontSize: 34, color: C.gold }}>{p.overall}<span style={{ fontSize: 18 }}>%</span></div>
            <div style={{ fontSize: 10, color: C.inkDim, textTransform: "uppercase", letterSpacing: "1.4px", marginTop: 4 }}>to hundo</div>
          </div>
        </div>
        <div style={{ flex: 1, minWidth: 240 }}>
          <div style={{ fontFamily: FONT_MONO, fontSize: 11, letterSpacing: "1.6px", textTransform: "uppercase", color: C.inkFaint }}>Profile audit</div>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(3,1fr)", gap: 11, marginTop: 12 }}>
            <div style={tileStat}><div style={tileNum}>{p.unlocked.toLocaleString()}</div><div style={tileCap}>unlocked</div></div>
            <div style={tileStat}><div style={tileNum}>{p.perfect}</div><div style={tileCap}>perfect games</div></div>
            <div style={tileStat}><div style={tileNum}>{p.started}<span style={{ color: C.inkFaint, fontSize: 15 }}>/{p.gamesWithAch}</span></div><div style={tileCap}>started</div></div>
          </div>
          {rarest && (
            <div style={{ marginTop: 13, fontSize: 13, color: C.inkDim }}>Rarest in your case — <span style={{ fontFamily: FONT_HEAD, fontWeight: 600, ...HOLO }}>{rarest.name}</span> <span style={{ fontFamily: FONT_MONO, color: C.inkFaint }}>{pctLabel(rarest.pct)}</span> · {rarest.game}</div>
          )}
        </div>
      </section>

      {/* COLLECTOR PROFILE */}
      <div style={{ display: "flex", gap: 14, flexWrap: "wrap", marginTop: 14 }}>
        <div style={{ ...panel, flex: 1, minWidth: 280, padding: 18 }}>
          <div style={{ fontFamily: FONT_MONO, fontSize: 11, letterSpacing: "1.6px", textTransform: "uppercase", color: C.inkFaint }}>Your collector profile</div>
          <div style={{ display: "flex", flexDirection: "column", gap: 11, marginTop: 14 }}>
            {([["common", "Common", C.common], ["uncommon", "Uncommon", C.uncommon], ["rare", "Rare", C.rare], ["ultra", "Ultra", C.ua]] as const).map(([k, label, col]) => (
              <div key={k}>
                <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 5 }}>
                  <span style={{ fontFamily: FONT_HEAD, fontSize: 12.5, color: col, fontWeight: 600 }}>{label}</span>
                  <span style={{ fontFamily: FONT_MONO, fontSize: 12, color: C.inkDim }}>{lib.mix[k].toLocaleString()}</span>
                </div>
                <div style={{ height: 8, background: C.case2, borderRadius: 5, overflow: "hidden" }}>
                  <div style={{ height: 8, width: `${(lib.mix[k] / mixMax * 100).toFixed(1)}%`, minWidth: lib.mix[k] > 0 ? 8 : 0, background: col, borderRadius: 5, transition: "width .4s" }} />
                </div>
              </div>
            ))}
          </div>
          <div style={{ marginTop: 15, fontSize: 12.5, color: C.inkDim, lineHeight: 1.5 }}>
            {elite}% of your unlocks are Rare or better — {elite >= 25 ? "you punch well above your weight." : elite >= 12 ? "a solid rare streak." : "plenty of headroom for trophy hunting."}
          </div>
        </div>
      </div>

      {/* FILTER RAIL */}
      <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap", margin: "26px 2px 14px" }}>
        <div style={sectionLabel}>The case</div>
        <div style={hr} />
        <div style={{ display: "flex", gap: 7, flexWrap: "wrap" }}>
          {(["all", "ultra", "rare", "uncommon", "common"] as FilterKey[]).map((k) => {
            const cnt = k === "all" ? lib.cards.length : lib.cards.filter((a) => tierOf(a.pct) === k).length;
            const col = k === "all" ? C.gold : tierColor(k as any);
            return <button key={k} style={chipBtn(filter === k, col)} onClick={() => setFilter(k)}>{k[0].toUpperCase() + k.slice(1)} <span style={{ opacity: 0.6, fontSize: 11 }}>{cnt}</span></button>;
          })}
        </div>
        <div style={{ display: "flex", gap: 7, marginLeft: 4 }}>
          {(["all", "unlocked", "locked"] as ShowKey[]).map((k) => (
            <button key={k} style={chipBtn(show === k, C.gold)} onClick={() => setShow(k)}>{k[0].toUpperCase() + k.slice(1)}</button>
          ))}
        </div>
      </div>

      {/* SEARCH + SORT */}
      <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap", margin: "0 2px 16px" }}>
        <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search achievements or games…"
          style={{ flex: 1, minWidth: 200, background: C.case, color: C.ink, border: `1px solid ${C.edge}`, borderRadius: 10, padding: "10px 13px", fontSize: 13.5, fontFamily: "'Inter',sans-serif", outline: "none" }} />
        <div style={{ display: "flex", gap: 6 }}>
          {(["rare", "common", "recent"] as SortKey[]).map((k) => (
            <button key={k} style={chipBtn(sort === k, C.gold)} onClick={() => setSort(k)}>{k === "rare" ? "Rarest" : k === "common" ? "Common" : "Recent"}</button>
          ))}
        </div>
        <select value={game} onChange={(e) => setGame(e.target.value)} style={{ background: C.case, color: C.ink, border: `1px solid ${C.edge}`, borderRadius: 10, padding: "9px 11px", fontSize: 12.5, fontFamily: "'Inter',sans-serif", outline: "none", maxWidth: 190, cursor: "pointer" }}>
          <option value="all">All games</option>
          {gameNames.map((g) => <option key={g} value={g}>{g}</option>)}
        </select>
        <div style={{ fontFamily: FONT_MONO, fontSize: 11, color: C.inkFaint, whiteSpace: "nowrap" }}>{shown.length} of {filtered.length}</div>
      </div>

      {/* GRID */}
      {shown.length > 0 ? (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill,minmax(248px,1fr))", gap: 14 }}>
          {shown.map((c, i) => <AchievementCard key={i} card={c} onClick={() => setSelected(c)} onGame={() => { const g = lib.games.find((x) => x.game === c.game); if (g) setGameView(g); }} />)}
        </div>
      ) : (
        <div style={{ textAlign: "center", color: C.inkFaint, padding: 50, fontFamily: FONT_MONO, fontSize: 13 }}>No achievements match — try a different search or filter.</div>
      )}
      {filtered.length > limit && (
        <div style={{ display: "flex", justifyContent: "center", marginTop: 18 }}>
          <button onClick={() => setLimit((l) => l + 30)} style={{ background: "linear-gradient(180deg,#12151f,#0e111a)", color: "#c2c9d6", border: `1px solid ${C.edge}`, borderRadius: 999, padding: "9px 20px", fontSize: 13, fontWeight: 600, cursor: "pointer", fontFamily: FONT_HEAD }}>Load more</button>
        </div>
      )}

      {/* LIBRARY */}
      <div ref={gamesRef} style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap", margin: "36px 2px 14px" }}>
        <div style={sectionLabel}>Your library</div><div style={hr} />
        <div style={{ fontFamily: FONT_MONO, fontSize: 11, color: C.inkFaint }}>{lib.games.length} games · tap to audit</div>
      </div>
      <div style={{ margin: "0 2px 16px" }}>
        <input value={gameSearch} onChange={(e) => setGameSearch(e.target.value)} placeholder="Search your library…"
          style={{ width: "100%", boxSizing: "border-box", background: C.case, color: C.ink, border: `1px solid ${C.edge}`, borderRadius: 10, padding: "10px 13px", fontSize: 13.5, fontFamily: "'Inter',sans-serif", outline: "none" }} />
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill,minmax(264px,1fr))", gap: 16 }}>
        {myGames.map((g) => (
          <div key={g.app} onClick={() => setGameView(g)} style={{ cursor: "pointer", ...panel, borderRadius: 14, overflow: "hidden" }}>
            <div style={{ position: "relative" }}>
              <img src={STEAM_HEADER(g.app)} alt="" loading="lazy" onError={onImgError} style={{ width: "100%", height: 82, objectFit: "cover", display: "block" }} />
              <div style={{ position: "absolute", inset: 0, background: "linear-gradient(180deg,transparent 40%,#0e111a)" }} />
            </div>
            <div style={{ padding: "11px 13px 13px" }}>
              <div style={{ fontFamily: FONT_HEAD, fontWeight: 600, fontSize: 14, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{g.game}</div>
              <div style={{ display: "flex", alignItems: "center", gap: 6, marginTop: 5, fontFamily: FONT_MONO, fontSize: 10.5, color: C.inkFaint }}>
                <span style={{ width: 5, height: 5, borderRadius: "50%", background: C.rare, flex: "none" }} />{g.play >= 1 ? `${g.play.toLocaleString()}h` : "<1h"} played
              </div>
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8, marginTop: 9 }}>
                <span style={{ fontFamily: FONT_MONO, fontSize: 11, color: C.inkDim }}>{g.unlocked}/{g.total}</span>
                <span style={{ fontFamily: FONT_MONO, fontSize: 11, color: C.gold }}>{g.pct}%</span>
              </div>
              <div style={{ height: 6, background: C.case2, border: `1px solid ${C.edge}`, borderRadius: 6, overflow: "hidden", marginTop: 8 }}>
                <div style={{ height: "100%", width: `${g.pct}%`, borderRadius: 5, background: g.pct >= 100 ? `linear-gradient(90deg,${C.uc},${C.uncommon})` : `linear-gradient(90deg,${C.goldLo},${C.gold})` }} />
              </div>
            </div>
          </div>
        ))}
      </div>

      {/* POPULAR RIGHT NOW (not owned) */}
      {popular.length > 0 && (
        <>
          <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap", margin: "36px 2px 14px" }}>
            <div style={sectionLabel}>🔥 Popular right now — not in your library</div><div style={hr} />
            <div style={{ fontFamily: FONT_MONO, fontSize: 11, color: C.inkFaint }}>most-played on Steam</div>
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill,minmax(260px,1fr))", gap: 14 }}>
            {popular.map((g) => (
              <div key={g.appid} style={{ ...panel, borderRadius: 14, overflow: "hidden" }}>
                <div style={{ position: "relative" }}>
                  <img src={STEAM_HEADER(g.appid)} alt="" loading="lazy" onError={onImgError} style={{ width: "100%", height: 110, objectFit: "cover", display: "block" }} />
                  <div style={{ position: "absolute", inset: 0, background: "linear-gradient(180deg,transparent 45%,#0e111a)" }} />
                </div>
                <div style={{ padding: "11px 14px 14px" }}>
                  <div style={{ fontFamily: FONT_HEAD, fontWeight: 600, fontSize: 15, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{g.name}</div>
                  <div style={{ display: "flex", gap: 7, marginTop: 11 }}>
                    <a href={`https://store.steampowered.com/app/${g.appid}/`} target="_blank" rel="noreferrer" style={{ flex: 1, textAlign: "center", textDecoration: "none", fontFamily: FONT_HEAD, fontSize: 11.5, fontWeight: 600, color: "#c2c9d6", background: C.case2, border: `1px solid ${C.edge}`, borderRadius: 8, padding: "6px 9px" }}>Steam store</a>
                    <button onClick={() => openRoadmap(g.name)} style={{ flex: 1, textAlign: "center", fontFamily: FONT_HEAD, fontSize: 11.5, fontWeight: 600, color: "#c2c9d6", background: C.case2, border: `1px solid ${C.edge}`, borderRadius: 8, padding: "6px 9px", cursor: "pointer" }}>Roadmap</button>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </>
      )}

      {/* POPULAR TO BEAT */}
      <div ref={beatRef} style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap", margin: "36px 2px 14px" }}>
        <div style={sectionLabel}>Popular on Steam to beat</div><div style={hr} />
        <div style={{ fontFamily: FONT_MONO, fontSize: 11, color: C.inkFaint }}>avg time · difficulty</div>
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill,minmax(264px,1fr))", gap: 16 }}>
        {BEAT_GAMES.map((g) => {
          const col = DIFF_COL[g.diff];
          return (
            <div key={g.appid} style={{ ...panel, borderRadius: 14, overflow: "hidden" }}>
              <div style={{ position: "relative" }}>
                <img src={STEAM_HEADER(g.appid)} alt="" loading="lazy" onError={onImgError} style={{ width: "100%", height: 94, objectFit: "cover", display: "block" }} />
                <div style={{ position: "absolute", inset: 0, background: "linear-gradient(180deg,transparent 35%,#0e111a)" }} />
                {owned.has(g.name.toLowerCase()) && (
                  <span style={{ position: "absolute", top: 8, right: 8, fontFamily: FONT_MONO, fontSize: 9, letterSpacing: ".5px", textTransform: "uppercase", padding: "3px 8px", borderRadius: 999, background: "rgba(10,12,18,.82)", border: `1px solid ${C.goldLo}`, color: C.gold }}>In your library</span>
                )}
              </div>
              <div style={{ padding: "11px 14px 14px" }}>
                <div style={{ fontFamily: FONT_HEAD, fontWeight: 600, fontSize: 15 }}>{g.name}</div>
                <p style={{ color: C.inkDim, fontSize: 12, lineHeight: 1.45, margin: "5px 0 0", display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical", overflow: "hidden" }}>{g.note}</p>
                <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8, marginTop: 11 }}>
                  <span style={{ fontFamily: FONT_MONO, fontSize: 12, color: C.ink }}>~{g.hours}h <span style={{ color: C.inkFaint }}>to 100%</span></span>
                  <span style={{ fontFamily: FONT_MONO, fontSize: 11, color: col }}>{DIFF_LAB[g.diff]}</span>
                </div>
                <div style={{ display: "flex", gap: 4, marginTop: 9 }}>
                  {[1, 2, 3, 4, 5].map((i) => <span key={i} style={{ flex: 1, height: 5, borderRadius: 3, background: i <= g.diff ? col : "#1f2433" }} />)}
                </div>
                <div style={{ display: "flex", gap: 7, marginTop: 11 }}>
                  <a href={`https://store.steampowered.com/app/${g.appid}/`} target="_blank" rel="noreferrer" style={{ flex: 1, textAlign: "center", textDecoration: "none", fontFamily: FONT_HEAD, fontSize: 11.5, fontWeight: 600, color: "#c2c9d6", background: C.case2, border: `1px solid ${C.edge}`, borderRadius: 8, padding: "6px 9px" }}>Steam store</a>
                  <button onClick={() => openRoadmap(g.name)} style={{ flex: 1, textAlign: "center", fontFamily: FONT_HEAD, fontSize: 11.5, fontWeight: 600, color: "#c2c9d6", background: C.case2, border: `1px solid ${C.edge}`, borderRadius: 8, padding: "6px 9px", cursor: "pointer" }}>Roadmap</button>
                </div>
              </div>
            </div>
          );
        })}
      </div>

      {selected && <CardModal card={selected} onClose={() => setSelected(null)} onAsk={() => askCurator(`How do I unlock "${selected.name}" in ${selected.game}?`)} onGame={() => { const g = lib.games.find((x) => x.game === selected.game); setSelected(null); if (g) setGameView(g); }} />}
      {gameView && <GameModal g={gameView} onClose={() => setGameView(null)} onRoadmap={() => openRoadmap(gameView.game)} onCard={(c) => { setGameView(null); setSelected(c); }} />}

      {!curatorOpen && (
        <button onClick={() => setCuratorOpen(true)} style={{ position: "fixed", right: 26, bottom: 26, zIndex: 40, display: "flex", alignItems: "center", gap: 9, padding: "13px 18px", border: "none", borderRadius: 999, cursor: "pointer", background: `linear-gradient(180deg,${C.gold},#c9991f)`, color: "#1a1303", fontFamily: FONT_HEAD, fontWeight: 700, fontSize: 14, letterSpacing: ".3px", boxShadow: "0 8px 24px -8px rgba(232,179,57,.6)" }}>✦ Ask the curator</button>
      )}
      <CuratorDrawer open={curatorOpen} onClose={() => setCuratorOpen(false)} steamId={session.steam_id} games={p.gamesTotal} inject={inject}
        greeting={`Welcome to your case — I've catalogued ${p.unlocked.toLocaleString()} unlocks across ${p.gamesWithAch} games, and you're ${p.overall}% of the way to a full Hundo. Ask me what's worth chasing next.`}
        starters={[
          { label: "🗺️ Build a roadmap to 100% …", fill: "Build me a roadmap to 100% " },
          { label: "⏱️ How long to 100% …", fill: "How long does it take to 100% " },
          { label: "❓ How do I unlock … in …", fill: "How do I unlock " },
          { label: "💎 My rarest achievements", q: "What are my top 3 rarest achievements?" },
        ]} />
    </div>
  );
}

// ── Card detail modal ─────────────────────────────────────────────────────────
function CardModal({ card, onClose, onAsk, onGame }: { card: Card; onClose: () => void; onAsk: () => void; onGame: () => void }) {
  const tier = tierOf(card.pct), tcol = tierColor(tier), isUltra = tier === "ultra", locked = !card.achieved;
  const pStyle: React.CSSProperties = { position: "relative", width: "min(620px,100%)", padding: 32, borderRadius: 18, background: "linear-gradient(180deg,#12151f,#0e111a)", border: `1px solid ${C.edge}`, boxShadow: "0 30px 80px -20px rgba(0,0,0,.7)" };
  if (isUltra) { pStyle.border = "1px solid transparent"; pStyle.background = `linear-gradient(180deg,#12151f,#0e111a) padding-box, linear-gradient(110deg,${C.ua},${C.ub},${C.uc},${C.ua}) border-box`; pStyle.backgroundSize = "100%, 240% 100%"; pStyle.animation = "foil 5.5s linear infinite"; }
  return (
    <div onClick={onClose} style={modalBg}>
      <div onClick={(e) => e.stopPropagation()} style={pStyle}>
        <button onClick={onClose} style={modalX}>✕</button>
        <div style={{ display: "flex", gap: 18, alignItems: "center" }}>
          <div style={{ flex: "none", width: 88, height: 88, borderRadius: 15, overflow: "hidden", background: C.case2, border: `1px solid ${tier === "common" ? C.edge : tcol}`, boxShadow: "0 0 0 3px #0a0c12" }}>
            <img src={card.icon} alt="" onError={onImgError} style={{ width: "100%", height: "100%", objectFit: "cover", filter: locked ? "saturate(.75)" : "none" }} />
          </div>
          <div style={{ minWidth: 0 }}>
            <div style={isUltra ? { fontFamily: FONT_HEAD, fontSize: 21, lineHeight: 1.2, ...HOLO } : { fontFamily: FONT_HEAD, fontWeight: 700, fontSize: 21, lineHeight: 1.2, color: C.ink }}>{card.name}</div>
            <button onClick={onGame} style={{ background: "none", border: "none", padding: 0, cursor: "pointer", color: C.inkDim, fontSize: 13, marginTop: 3 }}>{card.game} ↗</button>
          </div>
        </div>
        <p style={{ color: C.ink, fontSize: 14, lineHeight: 1.55, margin: "16px 0" }}>{card.desc || (card.hidden ? "🔒 Hidden achievement — Steam doesn't publish its steps. Ask the curator below for a guide." : "No description.")}</p>
        <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 16 }}>
          <span style={isUltra ? { fontFamily: FONT_MONO, fontSize: 12, padding: "2px 9px", borderRadius: 999, border: `1px solid ${C.ua}`, ...HOLO } : { fontFamily: FONT_MONO, fontSize: 12, fontWeight: 700, padding: "2px 9px", borderRadius: 999, border: `1px solid ${tcol}`, color: tcol }}>{pctLabel(card.pct)}</span>
          <span style={{ fontFamily: FONT_MONO, fontSize: 12.5, color: card.achieved ? C.gold : C.inkDim }}>
            {card.achieved ? (card.t ? "Unlocked " + new Date(card.t * 1000).toLocaleDateString(undefined, { year: "numeric", month: "long", day: "numeric" }) : "Unlocked") : `Not yet — ${pctLabel(card.pct)} of players have it`}
          </span>
        </div>
        <button onClick={onAsk} style={ctaBtn}>✦ Ask the curator how to get it</button>
      </div>
    </div>
  );
}

// ── Game drill-down modal ───────────────────────────────────────────────────────
function GameModal({ g, onClose, onRoadmap, onCard }: { g: LibGame; onClose: () => void; onRoadmap: () => void; onCard: (c: Card) => void }) {
  const circ = 2 * Math.PI * 32;
  const sorted = [...g.achievements].sort((a, b) =>
    (a.achieved === b.achieved) ? ((a.pct ?? 101) - (b.pct ?? 101)) : (a.achieved ? -1 : 1));
  return (
    <div onClick={onClose} style={modalBg}>
      <div onClick={(e) => e.stopPropagation()} style={{ position: "relative", width: "min(520px,100%)", maxHeight: "86vh", overflowY: "auto", borderRadius: 16, background: "linear-gradient(180deg,#12151f,#0e111a)", border: `1px solid ${C.edge}`, boxShadow: "0 30px 80px -20px rgba(0,0,0,.7)" }}>
        <button onClick={onClose} style={{ ...modalX, zIndex: 2 }}>✕</button>
        <img src={STEAM_HEADER(g.app)} alt="" onError={onImgError} style={{ width: "100%", height: 118, objectFit: "cover", display: "block" }} />
        <div style={{ padding: 20 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 15 }}>
            <div style={{ position: "relative", flex: "none", display: "grid", placeItems: "center" }}>
              <svg width="76" height="76" viewBox="0 0 76 76"><circle cx="38" cy="38" r="32" fill="none" stroke={C.case2} strokeWidth="7" /><circle cx="38" cy="38" r="32" fill="none" stroke={C.gold} strokeWidth="7" strokeLinecap="round" strokeDasharray={circ.toFixed(1)} strokeDashoffset={(circ * (1 - g.pct / 100)).toFixed(1)} transform="rotate(-90 38 38)" /></svg>
              <div style={{ position: "absolute", fontFamily: FONT_MONO, fontWeight: 700, fontSize: 15, color: C.gold }}>{Math.round(g.pct)}%</div>
            </div>
            <div>
              <div style={{ fontFamily: FONT_HEAD, fontWeight: 700, fontSize: 20 }}>{g.game}</div>
              <div style={{ fontFamily: FONT_MONO, fontSize: 12, color: C.inkDim, marginTop: 3 }}>{g.unlocked} / {g.total} unlocked</div>
            </div>
          </div>
          {g.unlocked < g.total && <button onClick={onRoadmap} style={{ ...ctaBtn, marginTop: 16 }}>✦ Build me a roadmap to 100%</button>}
          <div style={{ display: "flex", flexDirection: "column", gap: 6, marginTop: 16 }}>
            {sorted.map((a, i) => {
              const col = tierColor(tierOf(a.pct)); const clickable = !a.achieved;
              return (
                <div key={i} onClick={() => clickable && onCard(a)} style={{ display: "flex", alignItems: "flex-start", gap: 10, padding: "8px 9px", borderRadius: 9, background: C.case, border: `1px solid ${C.edge}`, opacity: a.achieved ? 1 : 0.62, cursor: clickable ? "pointer" : "default" }}>
                  <img src={a.icon} alt="" onError={onImgError} style={{ width: 34, height: 34, borderRadius: 7, objectFit: "cover", flex: "none", filter: a.achieved ? "none" : "saturate(.7)" }} />
                  <div style={{ minWidth: 0, flex: 1 }}>
                    <div style={{ fontFamily: FONT_HEAD, fontWeight: 600, fontSize: 13 }}>{a.name}{a.hidden ? " 🔒" : ""}</div>
                    <div style={{ color: C.inkDim, fontSize: 12, lineHeight: 1.4 }}>{a.desc || (a.hidden ? "🔒 Hidden — open it for a guide" : "")}</div>
                  </div>
                  <span style={{ fontFamily: FONT_MONO, fontSize: 10, fontWeight: 700, padding: "1px 7px", borderRadius: 999, border: `1px solid ${col}`, color: col, whiteSpace: "nowrap" }}>{pctLabel(a.pct)}</span>
                </div>
              );
            })}
          </div>
        </div>
      </div>
    </div>
  );
}

const modalBg: React.CSSProperties = { position: "fixed", inset: 0, zIndex: 50, display: "flex", alignItems: "center", justifyContent: "center", padding: 24, background: "rgba(5,7,12,.66)", backdropFilter: "blur(3px)" };
const modalX: React.CSSProperties = { position: "absolute", top: 14, right: 14, width: 32, height: 32, display: "grid", placeItems: "center", padding: 0, lineHeight: 1, fontSize: 15, background: "rgba(10,12,18,.6)", border: `1px solid ${C.edge}`, color: C.inkDim, borderRadius: 9, cursor: "pointer" };
const ctaBtn: React.CSSProperties = { width: "100%", padding: 12, border: "none", borderRadius: 10, cursor: "pointer", background: `linear-gradient(180deg,${C.gold},#c9991f)`, color: "#1a1303", fontFamily: FONT_HEAD, fontWeight: 700, fontSize: 14 };
