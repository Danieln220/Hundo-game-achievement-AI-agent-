import { useEffect, useMemo, useState } from "react";
import type { Card, LibGame, LibraryData } from "../types";
import { C, tierOf, tierColor, pctLabel, HOLO, onImgError } from "../tcTheme";

// "Next up" — the app's front door. Everything here is computed from the
// /library payload the shell already loaded (curator + games); no agent call,
// no new endpoint. The agent is one click away on every row.

const FONT_MONO = "'JetBrains Mono',monospace";
const FONT_HEAD = "'Chakra Petch',sans-serif";

const panel: React.CSSProperties = {
  background: "linear-gradient(180deg,#12151f,#0e111a)", border: `1px solid ${C.edge}`,
  borderRadius: 16, boxShadow: "inset 0 1px 0 rgba(255,255,255,.03)",
};
const label: React.CSSProperties = { fontFamily: FONT_MONO, fontSize: 10.5, letterSpacing: "1.6px", textTransform: "uppercase", color: C.gold };
const ghostBtn: React.CSSProperties = { padding: "7px 12px", border: `1px solid ${C.edge}`, borderRadius: 9, cursor: "pointer", background: C.case, color: "#c2c9d6", fontFamily: FONT_HEAD, fontWeight: 600, fontSize: 12, whiteSpace: "nowrap" };
const goldBtn: React.CSSProperties = { padding: "10px 16px", border: "none", borderRadius: 10, cursor: "pointer", background: `linear-gradient(180deg,${C.gold},#c9991f)`, color: "#1a1303", fontFamily: FONT_HEAD, fontWeight: 700, fontSize: 13.5, whiteSpace: "nowrap" };

const QUICK_WIN_COUNT = 4;

// Same key + shape as RoadmapView's checklist, so a tick here is a tick there.
const lsKey = (steamId: string, game: string) => `hundo_rm_${steamId}_${game.toLowerCase().replace(/\s+/g, "_")}`;
function loadChecked(steamId: string, game: string): string[] {
  try {
    const saved = JSON.parse(localStorage.getItem(lsKey(steamId, game)) || "[]");
    return Array.isArray(saved) ? saved : [];
  } catch { return []; }
}
function saveChecked(steamId: string, game: string, names: string[]) {
  try { localStorage.setItem(lsKey(steamId, game), JSON.stringify(names)); } catch { /* ignore */ }
}

// The game the plan is for: the curator's "closest to 100%", falling back to
// the most-complete unfinished game if the curator has nothing.
function pickNextGame(lib: LibraryData): LibGame | null {
  const c = lib.curator.closest;
  const byName = c ? lib.games.find((g) => g.game === c.game) : undefined;
  if (byName && byName.unlocked < byName.total) return byName;
  const open = lib.games.filter((g) => g.total > 0 && g.unlocked < g.total);
  if (!open.length) return null;
  return open.slice().sort((a, b) => b.pct - a.pct)[0];
}

const isMeta = (a: Card) => /\ball (the )?(achievements|trophies)\b/i.test(a.desc || "") || /\b(master|platinum)\b/i.test(a.name) && !a.desc;

export default function PlanHome({ lib, steamId, onRoadmap, onAsk, onCard, onGame, onOpenCase }: {
  lib: LibraryData; steamId: string;
  onRoadmap: (game: string) => void; onAsk: (q: string) => void;
  onCard: (c: Card) => void; onGame: (g: LibGame) => void; onOpenCase: () => void;
}) {
  const p = lib.profile;
  const next = useMemo(() => pickNextGame(lib), [lib]);

  // Locked achievements, easiest first (highest global unlock rate). The
  // meta-achievement ("obtain all …") goes last and isn't checkable — it
  // unlocks with the rest.
  const plan = useMemo(() => {
    if (!next) return { quick: [] as Card[], grind: [] as Card[], meta: null as Card | null };
    const locked = next.achievements.filter((a) => !a.achieved);
    const meta = locked.find(isMeta) ?? null;
    const rest = locked.filter((a) => a !== meta).sort((a, b) => (b.pct ?? -1) - (a.pct ?? -1));
    return { quick: rest.slice(0, QUICK_WIN_COUNT), grind: rest.slice(QUICK_WIN_COUNT), meta };
  }, [next]);

  const [checked, setChecked] = useState<Set<string>>(new Set());
  useEffect(() => { setChecked(new Set(next ? loadChecked(steamId, next.game) : [])); }, [steamId, next]);
  function toggle(name: string) {
    if (!next) return;
    const s = new Set(checked);
    if (s.has(name)) s.delete(name); else s.add(name);
    setChecked(s);
    saveChecked(steamId, next.game, [...s]);
  }
  const checkable = plan.quick.length + plan.grind.length;
  const done = [...plan.quick, ...plan.grind].filter((a) => checked.has(a.name)).length;

  const stalled = lib.curator.stalled.flatMap((s) => {
    const g = lib.games.find((x) => x.game === s.game);
    return g ? [{ ...s, g }] : [];
  });
  const quickAnywhere = lib.curator.quick.flatMap((q) => {
    const c = lib.cards.find((x) => x.name === q.name && x.game === q.game);
    return c ? [c] : [];
  }).slice(0, 4);
  const rarest = lib.curator.rarest[0];
  const circ = 2 * Math.PI * 32;
  const circSmall = 2 * Math.PI * 26;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16, marginTop: 16 }}>
      <div style={{ display: "flex", gap: 16, flexWrap: "wrap", alignItems: "flex-start" }}>

        {/* NEXT UP — the plan for the closest game */}
        <section style={{ ...panel, flex: "1 1 600px", minWidth: 0, border: `1px solid ${C.edgeLit}`, position: "relative", overflow: "hidden" }}>
          <div style={{ position: "absolute", inset: "0 0 auto 0", height: 2, background: `linear-gradient(90deg,transparent,${C.gold},transparent)`, opacity: 0.5 }} />
          {next ? (
            <>
              <div style={{ display: "flex", alignItems: "center", gap: 18, padding: "22px 22px 18px", borderBottom: `1px solid ${C.edge}`, flexWrap: "wrap" }}>
                <div style={{ position: "relative", display: "grid", placeItems: "center", flex: "none" }}>
                  <svg width="76" height="76" viewBox="0 0 76 76" aria-hidden="true">
                    <circle cx="38" cy="38" r="32" fill="none" stroke={C.case2} strokeWidth="7" />
                    <circle cx="38" cy="38" r="32" fill="none" stroke={C.gold} strokeWidth="7" strokeLinecap="round" strokeDasharray={circ.toFixed(1)} strokeDashoffset={(circ * (1 - next.pct / 100)).toFixed(1)} transform="rotate(-90 38 38)" />
                  </svg>
                  <span style={{ position: "absolute", fontFamily: FONT_MONO, fontWeight: 700, fontSize: 15, color: C.gold }}>{Math.round(next.pct)}%</span>
                </div>
                <div style={{ display: "flex", flexDirection: "column", gap: 4, flex: 1, minWidth: 200 }}>
                  <span style={label}>Next up · closest game to 100%</span>
                  <button onClick={() => onGame(next)} style={{ background: "none", border: "none", padding: 0, textAlign: "left", cursor: "pointer", fontFamily: FONT_HEAD, fontWeight: 700, fontSize: 24, lineHeight: 1.1, color: "#eef1f7" }}>{next.game}</button>
                  <span style={{ fontFamily: FONT_MONO, fontSize: 12, color: "#a9b2c6" }}>{next.unlocked} of {next.total} unlocked · {next.total - next.unlocked} left · quick wins first</span>
                </div>
                <div style={{ display: "flex", flexDirection: "column", gap: 8, flex: "none" }}>
                  <button onClick={() => onRoadmap(next.game)} style={goldBtn}>Build the full roadmap</button>
                  <button onClick={() => onGame(next)} style={{ ...ghostBtn, padding: "8px 16px", fontSize: 12.5 }}>Open in the case</button>
                </div>
              </div>

              <div style={{ ...label, color: C.uncommon, padding: "16px 22px 8px" }}>Quick wins · chase these first</div>
              {plan.quick.map((a) => {
                const col = tierColor(tierOf(a.pct));
                return (
                  <div key={a.name} style={{ display: "flex", alignItems: "center", gap: 14, flexWrap: "wrap", padding: "12px 22px", borderTop: "1px solid rgba(255,255,255,.05)", opacity: checked.has(a.name) ? 0.55 : 1 }}>
                    <input type="checkbox" className="rm-check" aria-label={`Done: ${a.name}`} checked={checked.has(a.name)} onChange={() => toggle(a.name)} />
                    <img src={a.icon} alt="" onError={onImgError} style={{ width: 44, height: 44, borderRadius: 9, objectFit: "cover", flex: "none", border: `1px solid ${C.edge}`, filter: "saturate(.8)" }} />
                    <button onClick={() => onCard(a)} style={{ flex: "1 1 200px", minWidth: 0, background: "none", border: "none", padding: 0, textAlign: "left", cursor: "pointer", color: C.ink }}>
                      <div style={{ fontFamily: FONT_HEAD, fontWeight: 600, fontSize: 15, textDecoration: checked.has(a.name) ? "line-through" : "none" }}>{a.name}</div>
                      <div style={{ fontSize: 12.5, lineHeight: 1.4, color: "#a9b2c6", marginTop: 3 }}>{a.desc || (a.hidden ? "Hidden — ask for the guide to see the steps." : "")}</div>
                    </button>
                    {/* chip + action wrap to their own line on narrow screens */}
                    <span style={{ display: "flex", alignItems: "center", gap: 10, marginLeft: "auto" }}>
                      <span style={{ fontFamily: FONT_MONO, fontWeight: 700, fontSize: 11.5, padding: "3px 9px", borderRadius: 999, border: `1px solid ${col}`, color: col, whiteSpace: "nowrap" }}>{pctLabel(a.pct)}</span>
                      <button onClick={() => onAsk(`How do I unlock "${a.name}" in ${a.game}?`)} style={ghostBtn}>How do I get this?</button>
                    </span>
                  </div>
                );
              })}

              {(plan.grind.length > 0 || plan.meta) && (
                <>
                  <div style={{ ...label, color: C.rare, padding: "16px 22px 8px", borderTop: "1px solid rgba(255,255,255,.05)" }}>Then the grind</div>
                  <div style={{ display: "flex", flexDirection: "column", padding: "0 22px 8px" }}>
                    {plan.grind.map((a) => {
                      const col = tierColor(tierOf(a.pct));
                      return (
                        <div key={a.name} style={{ display: "flex", alignItems: "center", gap: 14, padding: "9px 0", opacity: checked.has(a.name) ? 0.55 : 1 }}>
                          <input type="checkbox" className="rm-check" aria-label={`Done: ${a.name}`} checked={checked.has(a.name)} onChange={() => toggle(a.name)} />
                          <button onClick={() => onCard(a)} style={{ flex: 1, minWidth: 0, background: "none", border: "none", padding: 0, textAlign: "left", cursor: "pointer", fontSize: 13.5, color: "#cfd6e4" }}>
                            <span style={{ fontFamily: FONT_HEAD, fontWeight: 600, color: C.ink, textDecoration: checked.has(a.name) ? "line-through" : "none" }}>{a.name}</span>
                            <span style={{ color: "#7b8499" }}> · {a.desc || (a.hidden ? "hidden — ask for the guide to see the steps" : "")}</span>
                          </button>
                          <span style={{ fontFamily: FONT_MONO, fontWeight: 700, fontSize: 11.5, color: col, whiteSpace: "nowrap" }}>{pctLabel(a.pct)}</span>
                        </div>
                      );
                    })}
                    {plan.meta && (
                      <div style={{ display: "flex", alignItems: "center", gap: 14, padding: "9px 0" }}>
                        <span aria-hidden="true" style={{ width: 18, height: 18, flex: "none", borderRadius: 4, border: `1px dashed ${C.edgeLit}` }} />
                        <span style={{ flex: 1, minWidth: 0, fontSize: 13.5, color: "#cfd6e4" }}>
                          <span style={{ fontFamily: FONT_HEAD, fontWeight: 600, color: C.ink }}>{plan.meta.name}</span>
                          <span style={{ color: "#7b8499" }}> · {plan.meta.desc ? plan.meta.desc.toLowerCase().replace(/\.$/, "") + " — " : ""}unlocks with the rest</span>
                        </span>
                        <span style={{ fontFamily: FONT_MONO, fontWeight: 700, fontSize: 11.5, color: tierColor(tierOf(plan.meta.pct)), whiteSpace: "nowrap" }}>{pctLabel(plan.meta.pct)}</span>
                      </div>
                    )}
                  </div>
                </>
              )}

              <div style={{ display: "flex", justifyContent: "space-between", gap: 12, flexWrap: "wrap", padding: "12px 22px", borderTop: `1px solid ${C.edge}`, background: "rgba(10,12,18,.5)", fontFamily: FONT_MONO, fontSize: 11, color: C.inkDim }}>
                <span>{done} of {checkable} checked off · checks sync with the roadmap</span>
                <span style={{ color: C.uc }}>verified against your library</span>
              </div>
            </>
          ) : (
            <div style={{ padding: 32, textAlign: "center", color: C.inkDim, fontFamily: FONT_MONO, fontSize: 13 }}>
              Every game with achievements is at 100% — nothing left to plan. Ask the curator what to play next.
            </div>
          )}
        </section>

        {/* RIGHT RAIL */}
        <div style={{ display: "flex", flexDirection: "column", gap: 16, flex: "1 1 320px", maxWidth: 400, minWidth: 0 }}>

          <section style={{ ...panel, display: "flex", flexDirection: "column", gap: 10, padding: 18 }}>
            <span style={label}>Ask the curator</span>
            <div style={{ display: "flex", flexDirection: "column", gap: 7 }}>
              {["What am I closest to finishing?", "What's the easiest achievement I can get anywhere?", "What should I play next?"].map((q) => (
                <button key={q} onClick={() => onAsk(q)} style={{ textAlign: "left", padding: "10px 13px", border: `1px solid ${C.edge}`, borderRadius: 11, cursor: "pointer", background: C.case, color: C.ink, fontFamily: FONT_MONO, fontSize: 12 }}>&gt; {q.charAt(0).toLowerCase() + q.slice(1)}</button>
              ))}
            </div>
            <AskBox onAsk={onAsk} />
          </section>

          {stalled.length > 0 && (
            <section style={{ ...panel, display: "flex", flexDirection: "column", padding: 18 }}>
              <span style={{ ...label, marginBottom: 8 }}>Where you stalled</span>
              {stalled.map(({ g }) => (
                <div key={g.game} style={{ display: "flex", alignItems: "center", gap: 12, padding: "9px 0", borderTop: "1px solid rgba(255,255,255,.05)" }}>
                  <button onClick={() => onGame(g)} style={{ flex: 1, minWidth: 0, background: "none", border: "none", padding: 0, textAlign: "left", cursor: "pointer", fontFamily: FONT_HEAD, fontWeight: 600, fontSize: 14, color: C.ink, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{g.game}</button>
                  <span style={{ width: 52, height: 6, flex: "none", borderRadius: 3, background: C.case2, overflow: "hidden" }}><span style={{ display: "block", height: "100%", width: `${g.pct.toFixed(1)}%`, background: C.gold }} /></span>
                  <span style={{ fontFamily: FONT_MONO, fontSize: 11.5, color: "#a9b2c6", width: 44, textAlign: "right", flex: "none" }}>{g.pct.toFixed(1)}%</span>
                  <button onClick={() => onRoadmap(g.game)} style={{ ...ghostBtn, padding: "5px 9px", fontSize: 11.5, background: C.case2 }}>Roadmap</button>
                </div>
              ))}
            </section>
          )}

          <button onClick={onOpenCase} style={{ ...panel, display: "flex", alignItems: "center", gap: 16, padding: "16px 18px", cursor: "pointer", color: C.ink, textAlign: "left" }}>
            <span style={{ position: "relative", display: "grid", placeItems: "center", flex: "none" }}>
              <svg width="64" height="64" viewBox="0 0 64 64" aria-hidden="true">
                <circle cx="32" cy="32" r="26" fill="none" stroke={C.case2} strokeWidth="6" />
                <circle cx="32" cy="32" r="26" fill="none" stroke={C.gold} strokeWidth="6" strokeLinecap="round" strokeDasharray={circSmall.toFixed(1)} strokeDashoffset={(circSmall * (1 - p.overall / 100)).toFixed(1)} transform="rotate(-90 32 32)" />
              </svg>
              <span style={{ position: "absolute", fontFamily: FONT_MONO, fontWeight: 700, fontSize: 13, color: C.gold }}>{p.overall}%</span>
            </span>
            <span style={{ display: "flex", flexDirection: "column", gap: 3, flex: 1, minWidth: 0 }}>
              <span style={{ ...label, color: C.inkDim }}>Your case</span>
              <span style={{ fontFamily: FONT_HEAD, fontWeight: 700, fontSize: 16 }}>{p.unlocked.toLocaleString()} unlocked · {p.perfect} perfect {p.perfect === 1 ? "game" : "games"}</span>
              {rarest && <span style={{ fontSize: 12.5, color: "#a9b2c6" }}>Rarest pull: <span style={{ fontFamily: FONT_HEAD, fontWeight: 600, ...HOLO }}>{rarest.name}</span> · {pctLabel(rarest.pct)}</span>}
            </span>
            <span style={{ fontFamily: FONT_HEAD, fontWeight: 600, fontSize: 13, color: C.gold, whiteSpace: "nowrap" }}>Open →</span>
          </button>
        </div>
      </div>

      {/* QUICK WINS ANYWHERE */}
      {quickAnywhere.length > 0 && (
        <section style={{ ...panel, display: "flex", flexDirection: "column", gap: 12, padding: "18px 22px 20px" }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 12, flexWrap: "wrap" }}>
            <span style={label}>Quick wins anywhere in your library</span>
            <span style={{ fontFamily: FONT_MONO, fontSize: 11, color: C.inkDim }}>locked for you, unlocked by most players</span>
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill,minmax(240px,1fr))", gap: 12 }}>
            {quickAnywhere.map((c) => (
              <button key={`${c.game}::${c.name}`} onClick={() => onCard(c)} style={{ display: "flex", alignItems: "center", gap: 12, textAlign: "left", padding: 12, border: `1px solid ${C.edge}`, borderRadius: 12, cursor: "pointer", background: C.case, color: C.ink, minWidth: 0 }}>
                <img src={c.icon} alt="" onError={onImgError} style={{ width: 44, height: 44, borderRadius: 9, objectFit: "cover", flex: "none", border: `1px solid ${C.edge}` }} />
                <span style={{ display: "flex", flexDirection: "column", gap: 2, minWidth: 0 }}>
                  <span style={{ fontFamily: FONT_HEAD, fontWeight: 600, fontSize: 13.5, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{c.name}</span>
                  <span style={{ fontFamily: FONT_MONO, fontSize: 11, color: "#a9b2c6", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{c.game} · <span style={{ color: C.common }}>{pctLabel(c.pct)}</span></span>
                </span>
              </button>
            ))}
          </div>
        </section>
      )}
    </div>
  );
}

// Free-text question that goes straight into the curator drawer.
function AskBox({ onAsk }: { onAsk: (q: string) => void }) {
  const [q, setQ] = useState("");
  function send() {
    const t = q.trim();
    if (!t) return;
    onAsk(t);
    setQ("");
  }
  return (
    <form onSubmit={(e) => { e.preventDefault(); send(); }} style={{ display: "flex", gap: 8 }}>
      <label htmlFor="plan-ask" style={{ position: "absolute", width: 1, height: 1, overflow: "hidden", clip: "rect(0 0 0 0)" }}>Ask about your achievements</label>
      <input id="plan-ask" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Ask anything about your library…"
        style={{ flex: 1, minWidth: 0, height: 42, boxSizing: "border-box", padding: "0 13px", borderRadius: 11, border: `1px solid ${C.edge}`, background: C.panel2, color: C.ink, fontSize: 13.5, outline: "none" }} />
      <button type="submit" style={{ ...goldBtn, height: 42, padding: "0 14px", fontSize: 13 }}>Send</button>
    </form>
  );
}
