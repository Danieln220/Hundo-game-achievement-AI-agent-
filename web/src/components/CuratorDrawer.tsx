import { useEffect, useRef, useState } from "react";
import Chat from "./Chat";
import { getMemory, clearMemory } from "../api";
import { C } from "../tcTheme";

const FONT_HEAD = "'Chakra Petch',sans-serif";
const FONT_MONO = "'JetBrains Mono',monospace";

// Slide-in curator drawer (faithful to the prototype's drawerStyle). Hosts the
// real agent via <Chat/> — all answer/roadmap/audit rendering is reused.
export default function CuratorDrawer({ open, onClose, steamId, games, inject, greeting, starters }: {
  open: boolean; onClose: () => void; steamId: string; games: number;
  inject?: { q: string; nonce: number };
  greeting?: string; starters?: { label: string; q?: string; fill?: string }[];
}) {
  const [memOpen, setMemOpen] = useState(false);
  const [mem, setMem] = useState<string | null>(null);
  const asideRef = useRef<HTMLElement>(null);

  // aria-hidden alone still leaves the off-screen drawer tabbable — `inert`
  // removes it from both the tab order and the accessibility tree while closed.
  // (Set via ref: React 18's typings don't know the inert prop yet.)
  useEffect(() => {
    if (asideRef.current) asideRef.current.inert = !open;
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const h = (e: KeyboardEvent) => e.key === "Escape" && (memOpen ? setMemOpen(false) : onClose());
    document.addEventListener("keydown", h);
    return () => document.removeEventListener("keydown", h);
  }, [open, onClose, memOpen]);

  function toggleMem() {
    const next = !memOpen;
    setMemOpen(next);
    if (next) { setMem(null); getMemory(steamId).then((r) => setMem(r.memory || "")).catch(() => setMem("")); }
  }
  async function wipeMem() {
    await clearMemory(steamId).catch(() => {});
    setMem("");
  }

  return (
    <>
      {open && <div onClick={onClose} style={{ position: "fixed", inset: 0, zIndex: 45, background: "rgba(5,7,12,.55)", backdropFilter: "blur(2px)" }} />}
      <aside ref={asideRef} style={{
        position: "fixed", top: 0, right: 0, height: "100%", width: "min(420px,92vw)", zIndex: 46,
        display: "flex", flexDirection: "column", background: "linear-gradient(180deg,#0e111a,#0a0c12)",
        borderLeft: `1px solid ${C.edge}`, boxShadow: "-24px 0 60px -20px rgba(0,0,0,.7)",
        transform: open ? "translateX(0)" : "translateX(110%)", transition: "transform .32s cubic-bezier(.2,.8,.2,1)",
      }} aria-hidden={!open}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "18px 18px 14px", borderBottom: `1px solid ${C.edge}` }}>
          <div style={{ display: "flex", alignItems: "center", gap: 11 }}>
            <span style={{ width: 38, height: 38, display: "grid", placeItems: "center", borderRadius: 10, background: "linear-gradient(180deg,#1a1f2e,#12151f)", border: `1px solid ${C.edgeLit}`, fontSize: 17 }}>✦</span>
            <div>
              <div style={{ fontFamily: FONT_HEAD, fontWeight: 600, fontSize: 16 }}>The Curator</div>
              <div style={{ fontFamily: FONT_MONO, fontSize: 10, letterSpacing: "1px", textTransform: "uppercase", color: C.gold }}>knows your whole case</div>
            </div>
          </div>
          <div style={{ display: "flex", gap: 8 }}>
            <button onClick={toggleMem} title="What I remember about you"
              style={{ display: "grid", placeItems: "center", padding: 0, lineHeight: 1, background: memOpen ? C.case2 : "transparent", border: `1px solid ${memOpen ? C.goldLo : C.edge}`, color: memOpen ? C.gold : C.inkDim, borderRadius: 9, width: 32, height: 32, cursor: "pointer", fontSize: 15 }}>🧠</button>
            <button onClick={onClose} style={{ display: "grid", placeItems: "center", padding: 0, lineHeight: 1, background: "transparent", border: `1px solid ${C.edge}`, color: C.inkDim, borderRadius: 9, width: 32, height: 32, cursor: "pointer", fontSize: 15 }}>✕</button>
          </div>
        </div>
        {memOpen && (
          <div style={{ padding: "12px 16px", borderBottom: `1px solid ${C.edge}`, background: "rgba(232,179,57,0.04)" }}>
            <div style={{ fontFamily: FONT_MONO, fontSize: 10, letterSpacing: "1px", textTransform: "uppercase", color: C.gold, marginBottom: 7 }}>What I remember about you</div>
            {mem === null ? (
              <div style={{ color: C.inkDim, fontSize: 13 }}>Loading…</div>
            ) : mem.trim() ? (
              <div style={{ color: C.ink, fontSize: 13, lineHeight: 1.5, whiteSpace: "pre-wrap" }}>{mem}</div>
            ) : (
              <div style={{ color: C.inkFaint, fontSize: 13, fontStyle: "italic" }}>Nothing yet — I'll pick up your goals and preferences as we chat.</div>
            )}
            <div style={{ display: "flex", justifyContent: "flex-end", marginTop: 9 }}>
              <button onClick={wipeMem} disabled={!mem || !mem.trim()}
                style={{ background: "transparent", border: `1px solid ${C.edge}`, color: C.inkDim, borderRadius: 8, padding: "4px 11px", fontSize: 12, cursor: mem && mem.trim() ? "pointer" : "default", fontFamily: FONT_HEAD, opacity: mem && mem.trim() ? 1 : 0.5 }}>Clear memory</button>
            </div>
          </div>
        )}
        {/* Kept MOUNTED across open/close so the conversation persists and the
            injected question isn't re-fired on every reopen (remount would re-run
            Chat's inject effect). The drawer just slides off-screen when closed. */}
        <div className="curator-body" style={{ flex: 1, minHeight: 0, display: "flex", padding: "0 16px" }}>
          <Chat steamId={steamId} games={games} inject={inject} greeting={greeting} starters={starters} active={open} />
        </div>
      </aside>
    </>
  );
}
