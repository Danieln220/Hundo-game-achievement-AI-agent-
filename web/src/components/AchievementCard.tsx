import type { Card } from "../types";
import { C, tierOf, tierColor, pctLabel, HOLO, onImgError, IMG_FALLBACK } from "../tcTheme";

// One collectible card — faithful port of the prototype: rarity border/glow,
// ultra foil (border-box gradient + holo name), 3D tilt + cursor glare.
export default function AchievementCard({ card, onClick, onGame }: {
  card: Card;
  onClick?: () => void;
  onGame?: () => void;
}) {
  const tier = tierOf(card.pct);
  const tcol = tierColor(tier);
  const locked = !card.achieved;
  const isUltra = tier === "ultra";

  const cardStyle: React.CSSProperties = {
    position: "relative", display: "flex", gap: 13, padding: 14,
    background: "linear-gradient(180deg,#12151f,#0e111a)", border: `1px solid ${C.edge}`,
    borderRadius: 14, boxShadow: "inset 0 1px 0 rgba(255,255,255,.03)", overflow: "hidden",
    cursor: "pointer", textAlign: "left", width: "100%",
    transition: "transform .22s cubic-bezier(.2,.8,.2,1), box-shadow .25s",
    transformStyle: "preserve-3d", willChange: "transform", opacity: locked ? 0.6 : 1,
  };
  if (tier === "uncommon") { cardStyle.border = "1px solid #33525e"; cardStyle.boxShadow = `inset 0 1px 0 rgba(255,255,255,.03), 0 0 18px -9px ${C.uncommon}`; }
  if (tier === "rare") { cardStyle.border = "1px solid #4a4275"; cardStyle.boxShadow = `inset 0 1px 0 rgba(255,255,255,.03), 0 0 22px -8px ${C.rare}`; }
  if (isUltra) {
    cardStyle.border = "1px solid transparent";
    cardStyle.background = `linear-gradient(180deg,#12151f,#0e111a) padding-box, linear-gradient(110deg,${C.ua},${C.ub},${C.uc},${C.ua}) border-box`;
    cardStyle.backgroundSize = "100%, 240% 100%";
    cardStyle.boxShadow = "inset 0 1px 0 rgba(255,255,255,.03), 0 0 26px -7px rgba(232,106,200,.5)";
    cardStyle.animation = "foil 5.5s linear infinite";
  }

  const iconBox: React.CSSProperties = {
    flex: "none", width: 56, height: 56, borderRadius: 11, overflow: "hidden",
    background: "linear-gradient(180deg,#1a1f2e,#12151f)",
    border: `1px solid ${tier === "common" ? C.edge : tcol}`, boxShadow: "0 0 0 3px #0a0c12",
    position: "relative", zIndex: 1,
  };
  if (isUltra) {
    iconBox.border = "1px solid transparent";
    iconBox.background = `linear-gradient(180deg,#1a1f2e,#12151f) padding-box, linear-gradient(110deg,${C.ua},${C.ub},${C.uc},${C.ua}) border-box`;
    iconBox.backgroundSize = "100%, 240% 100%";
  }

  const nameStyle: React.CSSProperties = isUltra
    ? { fontFamily: "'Chakra Petch',sans-serif", fontSize: 15, lineHeight: 1.25, ...HOLO }
    : { fontFamily: "'Chakra Petch',sans-serif", fontWeight: 600, fontSize: 15, lineHeight: 1.25, color: C.ink };

  const chipStyle: React.CSSProperties = isUltra
    ? { fontFamily: "'JetBrains Mono',monospace", fontSize: 11, padding: "1px 8px", borderRadius: 999, whiteSpace: "nowrap", border: `1px solid ${C.ua}`, ...HOLO }
    : { fontFamily: "'JetBrains Mono',monospace", fontSize: 11, fontWeight: 700, padding: "1px 8px", borderRadius: 999, whiteSpace: "nowrap", border: `1px solid ${tcol}`, color: tcol };

  function tiltMove(e: React.MouseEvent<HTMLElement>) {
    const el = e.currentTarget;
    const r = el.getBoundingClientRect();
    const px = (e.clientX - r.left) / r.width, py = (e.clientY - r.top) / r.height;
    el.style.setProperty("--mx", (px * 100).toFixed(1) + "%");
    el.style.setProperty("--my", (py * 100).toFixed(1) + "%");
    el.style.setProperty("--glare", "1");
    el.style.transform = `perspective(900px) rotateX(${((0.5 - py) * 11).toFixed(2)}deg) rotateY(${((px - 0.5) * 15).toFixed(2)}deg) translateY(-5px)`;
  }
  function tiltLeave(e: React.MouseEvent<HTMLElement>) {
    e.currentTarget.style.transform = "";
    e.currentTarget.style.setProperty("--glare", "0");
  }

  return (
    <div style={cardStyle} tabIndex={0} onClick={onClick} onMouseMove={tiltMove} onMouseLeave={tiltLeave}
      onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onClick?.(); } }}>
      <div style={iconBox}>
        <img src={card.icon || IMG_FALLBACK} alt="" loading="lazy" onError={onImgError}
          style={{ width: "100%", height: "100%", objectFit: "cover", borderRadius: 10, display: "block", filter: locked ? "saturate(.75)" : "none" }} />
      </div>
      <div style={{ minWidth: 0, flex: 1, position: "relative", zIndex: 1 }}>
        <div style={nameStyle}>{card.name}</div>
        <p style={{ color: C.inkDim, fontSize: 12.5, margin: "3px 0 0", lineHeight: 1.45, display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical", overflow: "hidden" }}>
          {card.desc || (card.hidden ? "🔒 Hidden — its steps aren't on Steam" : "")}
        </p>
        <div style={{ display: "flex", alignItems: "center", gap: 9, marginTop: 10 }}>
          <span style={chipStyle}>{pctLabel(card.pct)}</span>
          <span style={{ fontSize: 11, color: C.inkFaint, fontFamily: "'JetBrains Mono',monospace" }}>{locked ? "locked" : tier}</span>
          <span onClick={(e) => { e.stopPropagation(); onGame?.(); }}
            style={{ marginLeft: "auto", fontSize: 11, color: C.inkFaint, fontFamily: "'Inter',sans-serif", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis", maxWidth: 92 }}>
            {card.game}
          </span>
        </div>
      </div>
      <div style={{ position: "absolute", inset: 0, borderRadius: 14, pointerEvents: "none", zIndex: 4, mixBlendMode: "screen",
        background: "radial-gradient(360px circle at var(--mx,50%) var(--my,50%), rgba(255,255,255,.20), transparent 42%)",
        opacity: "var(--glare,0)" as unknown as number, transition: "opacity .25s" }} />
    </div>
  );
}
