import { useEffect } from "react";
import Chat from "./Chat";
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
  useEffect(() => {
    if (!open) return;
    const h = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    document.addEventListener("keydown", h);
    return () => document.removeEventListener("keydown", h);
  }, [open, onClose]);

  return (
    <>
      {open && <div onClick={onClose} style={{ position: "fixed", inset: 0, zIndex: 45, background: "rgba(5,7,12,.55)", backdropFilter: "blur(2px)" }} />}
      <aside style={{
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
          <button onClick={onClose} style={{ display: "grid", placeItems: "center", padding: 0, lineHeight: 1, background: "transparent", border: `1px solid ${C.edge}`, color: C.inkDim, borderRadius: 9, width: 32, height: 32, cursor: "pointer", fontSize: 15 }}>✕</button>
        </div>
        {open && (
          <div className="curator-body" style={{ flex: 1, minHeight: 0, display: "flex", padding: "0 16px" }}>
            <Chat steamId={steamId} games={games} inject={inject} greeting={greeting} starters={starters} />
          </div>
        )}
      </aside>
    </>
  );
}
