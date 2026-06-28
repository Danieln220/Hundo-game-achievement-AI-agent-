import { useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { askStream, ask } from "../api";
import type { Achievement, LibGame, RoadmapData } from "../types";
import { C, tierColor, tierOf, pctLabel, STEAM_HEADER, onImgError } from "../tcTheme";

const FONT_HEAD = "'Chakra Petch',sans-serif";
const FONT_MONO = "'JetBrains Mono',monospace";

const NODE_LABEL: Record<string, string> = {
  inspect_schema: "Reading your save data", plan_code: "Plotting the route",
  execute_code: "Verifying achievements", validate_output: "Checking", roadmap: "Charting the quest",
};

type GuideState = { loading: boolean; answer?: string; sources?: { title: string; url: string }[] };

export default function RoadmapView({ steamId, library, initialGame, onClose }: {
  steamId: string; library: LibGame[]; initialGame?: string; onClose: () => void;
}) {
  const [game, setGame] = useState(initialGame ?? "");
  const [data, setData] = useState<RoadmapData | null>(null);
  const [loading, setLoading] = useState(false);
  const [progress, setProgress] = useState<string>("");
  const [error, setError] = useState<string | null>(null);
  const [pick, setPick] = useState("");
  const [checked, setChecked] = useState<Set<string>>(new Set());
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [guides, setGuides] = useState<Record<string, GuideState>>({});
  const [hideMP, setHideMP] = useState(false);
  const [hideDLC, setHideDLC] = useState(false);
  const [onlyMissable, setOnlyMissable] = useState(false);
  const [easyOnly, setEasyOnly] = useState(false);
  const abortRef = useRef<AbortController | null>(null);

  const lsKey = (target: string) => `hundo_rm_${steamId}_${target.toLowerCase().replace(/\s+/g, "_")}`;

  async function build(g: string) {
    const target = g.trim();
    if (!target || loading) return;
    setGame(target); setData(null); setError(null); setLoading(true); setProgress("");
    const ctrl = new AbortController(); abortRef.current = ctrl;
    try {
      const res = await askStream(`Build me a roadmap to 100% ${target}`, steamId, [],
        (node) => setProgress(NODE_LABEL[node] ?? "Working"), ctrl.signal);
      if (res.roadmap) {
        setData(res.roadmap);
        try {
          const saved = JSON.parse(localStorage.getItem(lsKey(res.roadmap.target)) || "[]");
          setChecked(new Set(saved));
        } catch { setChecked(new Set()); }
      } else {
        setError(res.answer || "Couldn't build a roadmap for that game.");
      }
    } catch (e) {
      setError((e as Error).name === "AbortError" ? null : (e as Error).message);
    } finally { setLoading(false); abortRef.current = null; }
  }

  useEffect(() => { if (initialGame) build(initialGame); /* eslint-disable-next-line */ }, []);

  function toggleCheck(name: string) {
    setChecked((prev) => {
      const next = new Set(prev);
      next.has(name) ? next.delete(name) : next.add(name);
      if (data) localStorage.setItem(lsKey(data.target), JSON.stringify([...next]));
      return next;
    });
  }
  const flip = (set: Set<string>, k: string, setter: (s: Set<string>) => void) => {
    const n = new Set(set); n.has(k) ? n.delete(k) : n.add(k); setter(n);
  };

  async function loadGuide(a: Achievement) {
    if (guides[a.name]?.answer || guides[a.name]?.loading || !data) return;
    setGuides((g) => ({ ...g, [a.name]: { loading: true } }));
    try {
      const r = await ask(`How do I unlock "${a.name}" in ${data.target}?`, steamId, []);
      setGuides((g) => ({ ...g, [a.name]: { loading: false, answer: r.answer, sources: r.sources } }));
    } catch {
      setGuides((g) => ({ ...g, [a.name]: { loading: false, answer: "Couldn't load a guide right now." } }));
    }
  }

  const phases = useMemo(() => {
    if (!data?.phases) return [];
    const vis = (a: Achievement) =>
      !(hideMP && a.category === "multiplayer") &&
      !(hideDLC && a.category === "dlc") &&
      !(onlyMissable && !a.missable) &&
      !(easyOnly && !(a.rarity_pct != null && a.rarity_pct >= 30));
    return data.phases.map((ph) => ({ ...ph, achievements: ph.achievements.filter(vis) }))
      .filter((ph) => ph.achievements.length > 0);
  }, [data, hideMP, hideDLC, onlyMissable, easyOnly]);

  const allShown = useMemo(() => (data?.phases ?? []).flatMap((p) => p.achievements), [data]);
  const doneCount = allShown.filter((a) => checked.has(a.name)).length;

  // ── Picker (no game chosen yet) ──
  if (!data && !loading && !error) {
    const list = (pick.trim() ? library.filter((g) => g.game.toLowerCase().includes(pick.trim().toLowerCase())) : library)
      .filter((g) => g.pct < 100).slice(0, 24);
    return (
      <div style={wrap}>
        <Header title="Roadmaps" sub="Pick a game — or type any title" onClose={onClose} />
        <div style={{ display: "flex", gap: 9, margin: "18px 0" }}>
          <input value={pick} onChange={(e) => setPick(e.target.value)} onKeyDown={(e) => e.key === "Enter" && pick.trim() && build(pick)}
            placeholder="Search your library, or type any game…" style={input} />
          <button onClick={() => build(pick)} disabled={!pick.trim()} style={goBtn}>Build</button>
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill,minmax(216px,1fr))", gap: 14 }}>
          {list.map((g) => (
            <button key={g.app} onClick={() => build(g.game)} style={{ ...gameCard, cursor: "pointer", textAlign: "left", padding: 0 }}>
              <div style={{ position: "relative" }}>
                <img src={STEAM_HEADER(g.app)} alt="" loading="lazy" onError={onImgError} style={{ width: "100%", height: 82, objectFit: "cover", display: "block" }} />
                <div style={{ position: "absolute", inset: 0, background: "linear-gradient(180deg,transparent 40%,#0e111a)" }} />
              </div>
              <div style={{ padding: "11px 13px 13px" }}>
                <div style={{ fontFamily: FONT_HEAD, fontWeight: 600, fontSize: 14, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{g.game}</div>
                <div style={{ fontFamily: FONT_MONO, fontSize: 11, color: C.gold, marginTop: 6 }}>{g.pct}% · {g.total - g.unlocked} to go</div>
              </div>
            </button>
          ))}
        </div>
      </div>
    );
  }

  // ── Building / error ──
  if (loading) return <div style={wrap}><Header title={game} sub="Building your plan…" onClose={onClose} /><div style={{ display: "flex", alignItems: "center", gap: 11, color: C.gold, fontFamily: FONT_MONO, padding: "48px 0" }}><span className="spinner" /> {progress || "Charting the quest"}…</div></div>;
  if (error) return <div style={wrap}><Header title={game} sub="" onClose={onClose} /><div style={{ color: C.inkDim, padding: "30px 0" }}>{error}</div><button onClick={() => { setError(null); setGame(""); }} style={goBtn}>Try another game</button></div>;
  if (!data) return null;

  const circ = 2 * Math.PI * 32;
  return (
    <div style={wrap}>
      <Header title={data.target} sub={`${data.unlocked}/${data.total} unlocked · ${data.remaining} to go`} onClose={onClose} />

      {/* progress + plan checklist */}
      <div style={{ display: "flex", gap: 22, alignItems: "center", flexWrap: "wrap", marginTop: 16, padding: 18, background: "linear-gradient(180deg,#12151f,#0e111a)", border: `1px solid ${C.edge}`, borderRadius: 16 }}>
        <div style={{ position: "relative", display: "grid", placeItems: "center" }}>
          <svg width="76" height="76" viewBox="0 0 76 76"><circle cx="38" cy="38" r="32" fill="none" stroke={C.case2} strokeWidth="7" /><circle cx="38" cy="38" r="32" fill="none" stroke={C.gold} strokeWidth="7" strokeLinecap="round" strokeDasharray={circ.toFixed(1)} strokeDashoffset={(circ * (1 - data.pct_done / 100)).toFixed(1)} transform="rotate(-90 38 38)" /></svg>
          <div style={{ position: "absolute", fontFamily: FONT_MONO, fontWeight: 700, fontSize: 15, color: C.gold }}>{Math.round(data.pct_done)}%</div>
        </div>
        <div style={{ flex: 1, minWidth: 200 }}>
          <div style={{ fontFamily: FONT_MONO, fontSize: 11, color: C.inkFaint, textTransform: "uppercase", letterSpacing: "1px" }}>Plan progress (this session)</div>
          <div style={{ height: 8, background: C.case2, borderRadius: 5, overflow: "hidden", margin: "8px 0 5px" }}>
            <div style={{ height: "100%", width: `${allShown.length ? (doneCount / allShown.length * 100) : 0}%`, background: `linear-gradient(90deg,${C.goldLo},${C.gold})`, transition: "width .3s" }} />
          </div>
          <div style={{ fontFamily: FONT_MONO, fontSize: 12, color: C.inkDim }}>{doneCount} of {allShown.length} planned achievements checked off</div>
        </div>
      </div>

      {/* toggles */}
      <div style={{ display: "flex", gap: 7, flexWrap: "wrap", margin: "16px 0" }}>
        {([["Easy only", easyOnly, setEasyOnly], ["Only missables", onlyMissable, setOnlyMissable], ["Hide multiplayer", hideMP, setHideMP], ["Hide DLC", hideDLC, setHideDLC]] as const).map(([label, on, set]) => (
          <button key={label} onClick={() => set(!on)} style={toggle(on)}>{label}</button>
        ))}
      </div>

      <div style={{ fontSize: 12.5, color: C.inkFaint, marginBottom: 14, fontStyle: "italic" }}>
        Suggested game plan — phases are AI-grouped from each achievement's description (the list itself is verified). ⚠️ missables are best-effort; double-check before a point of no return.
      </div>

      {/* phases */}
      {phases.map((ph) => {
        const isCollapsed = collapsed.has(ph.key);
        return (
          <div key={ph.key} style={{ marginBottom: 14, border: `1px solid ${ph.warn ? "rgba(232,179,57,.4)" : C.edge}`, borderRadius: 14, overflow: "hidden", background: "linear-gradient(180deg,#12151f,#0e111a)" }}>
            <button onClick={() => flip(collapsed, ph.key, setCollapsed)} style={{ width: "100%", display: "flex", alignItems: "center", justifyContent: "space-between", padding: "13px 16px", background: ph.warn ? "rgba(232,179,57,.06)" : "transparent", border: "none", cursor: "pointer", color: C.ink }}>
              <span style={{ fontFamily: FONT_HEAD, fontWeight: 600, fontSize: 15 }}>{ph.title} <span style={{ fontFamily: FONT_MONO, fontSize: 11, color: C.inkFaint, background: C.case2, borderRadius: 999, padding: "1px 8px", marginLeft: 6 }}>{ph.achievements.length}</span></span>
              <span style={{ color: C.inkFaint }}>{isCollapsed ? "▸" : "▾"}</span>
            </button>
            {!isCollapsed && (
              <div style={{ padding: "0 12px 12px" }}>
                {ph.achievements.map((a, i) => {
                  const done = checked.has(a.name); const isOpen = expanded.has(a.name); const tcol = tierColor(tierOf(a.rarity_pct)); const guide = guides[a.name];
                  return (
                    <div key={i} style={{ borderTop: `1px solid ${C.edge}` }}>
                      <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "9px 4px" }}>
                        <input type="checkbox" className="rm-check" checked={done} onChange={() => toggleCheck(a.name)} />
                        <button onClick={() => flip(expanded, a.name, setExpanded)} style={{ flex: 1, minWidth: 0, textAlign: "left", background: "none", border: "none", cursor: "pointer", color: done ? C.inkFaint : C.ink, fontFamily: FONT_HEAD, fontWeight: 500, fontSize: 13.5, textDecoration: done ? "line-through" : "none", display: "flex", alignItems: "center", gap: 7 }}>
                          <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{a.name}</span>
                          {a.hidden && <span style={{ fontFamily: FONT_MONO, fontSize: 9.5, color: C.inkFaint, border: `1px solid ${C.edge}`, borderRadius: 999, padding: "0 6px", flex: "none" }}>🔒 hidden</span>}
                          {a.missable && <span style={{ fontFamily: FONT_MONO, fontSize: 9.5, color: C.gold, border: `1px solid ${C.goldLo}`, borderRadius: 999, padding: "0 6px", flex: "none" }}>⚠ missable</span>}
                        </button>
                        <span style={{ fontFamily: FONT_MONO, fontSize: 10.5, fontWeight: 700, color: tcol, border: `1px solid ${tcol}`, borderRadius: 999, padding: "1px 7px", flex: "none" }}>{pctLabel(a.rarity_pct)}</span>
                        <span style={{ color: C.inkFaint, fontSize: 12, cursor: "pointer", flex: "none" }} onClick={() => flip(expanded, a.name, setExpanded)}>{isOpen ? "▾" : "▸"}</span>
                      </div>
                      {isOpen && (
                        <div style={{ padding: "0 4px 12px 31px" }}>
                          {a.description
                            ? <p style={{ color: C.inkDim, fontSize: 13, lineHeight: 1.5, margin: "0 0 10px" }}>{a.description}</p>
                            : a.hidden && <p style={{ color: C.inkFaint, fontSize: 13, lineHeight: 1.5, margin: "0 0 10px", fontStyle: "italic" }}>🔒 Hidden — Steam doesn't publish its steps; load a guide below.</p>}
                          {!guide && <button onClick={() => loadGuide(a)} style={guideBtn}>↗ Load a guide</button>}
                          {guide?.loading && <div style={{ color: C.gold, fontFamily: FONT_MONO, fontSize: 12.5, display: "flex", gap: 8, alignItems: "center" }}><span className="spinner" /> finding a guide…</div>}
                          {guide?.answer && (
                            <div style={{ background: C.case, border: `1px solid ${C.edge}`, borderRadius: 10, padding: "11px 13px" }}>
                              <div className="markdown" style={{ fontSize: 13.5, lineHeight: 1.5 }}>
                                <ReactMarkdown remarkPlugins={[remarkGfm]} components={{ a: ({ href, children }) => <a href={href} target="_blank" rel="noreferrer" style={{ color: C.gold }}>{children}</a> }}>{guide.answer}</ReactMarkdown>
                              </div>
                              {!!guide.sources?.length && (
                                <ol style={{ margin: "8px 0 0", paddingLeft: 20, fontSize: 12.5 }}>
                                  {guide.sources.map((s, j) => <li key={j}><a href={s.url} target="_blank" rel="noreferrer" style={{ color: C.gold }}>{s.title || s.url}</a></li>)}
                                </ol>
                              )}
                            </div>
                          )}
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        );
      })}
      {phases.length === 0 && <div style={{ color: C.inkFaint, padding: 30, textAlign: "center", fontFamily: FONT_MONO }}>Nothing matches those filters.</div>}
    </div>
  );
}

function Header({ title, sub, onClose }: { title: string; sub: string; onClose: () => void }) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 14, padding: "20px 2px 16px", borderBottom: `1px solid ${C.edge}` }}>
      <button onClick={onClose} style={{ background: "transparent", border: `1px solid ${C.edge}`, color: C.inkDim, borderRadius: 9, padding: "7px 12px", cursor: "pointer", fontFamily: FONT_HEAD, fontSize: 13 }}>← Back</button>
      <div>
        <h1 style={{ margin: 0, fontFamily: FONT_HEAD, fontSize: 22, fontWeight: 700 }}>🗺️ {title}</h1>
        {sub && <div style={{ fontFamily: FONT_MONO, fontSize: 12, color: C.inkDim, marginTop: 2 }}>{sub}</div>}
      </div>
    </div>
  );
}

const wrap: React.CSSProperties = { maxWidth: 1040, margin: "0 auto", padding: "0 32px 110px", width: "100%" };
const input: React.CSSProperties = { flex: 1, background: C.case, color: C.ink, border: `1px solid ${C.edge}`, borderRadius: 10, padding: "11px 14px", fontSize: 14, fontFamily: "'Inter',sans-serif", outline: "none" };
const goBtn: React.CSSProperties = { background: `linear-gradient(180deg,${C.gold},#c9991f)`, color: "#1a1303", border: "none", borderRadius: 10, padding: "11px 18px", fontFamily: FONT_HEAD, fontWeight: 700, cursor: "pointer" };
const gameCard: React.CSSProperties = { background: "linear-gradient(180deg,#12151f,#0e111a)", border: `1px solid ${C.edge}`, borderRadius: 14, overflow: "hidden" };
const guideBtn: React.CSSProperties = { background: C.case2, color: "#c2c9d6", border: `1px solid ${C.edge}`, borderRadius: 8, padding: "6px 12px", fontFamily: FONT_HEAD, fontSize: 12.5, fontWeight: 600, cursor: "pointer" };
const toggle = (on: boolean): React.CSSProperties => ({ background: on ? "linear-gradient(180deg,#1a1f2e,#12151f)" : "transparent", color: on ? C.gold : C.inkDim, border: `1px solid ${on ? C.goldLo : C.edge}`, borderRadius: 999, padding: "6px 13px", fontFamily: FONT_HEAD, fontSize: 12.5, fontWeight: 600, cursor: "pointer" });
