// Shared palette + rarity logic for "The Trophy Case" — values lifted verbatim
// from the Claude Design prototype so the port matches exactly.
export const C = {
  gold: "#e8b339", goldLo: "#a07d22",
  common: "#7e8aa0", uncommon: "#4fb6c9", rare: "#8b7bf0",
  ua: "#f0c04a", ub: "#e86ac8", uc: "#5be0d0", // holo stops
  ink: "#e8ecf4", inkDim: "#8a93a8", inkFaint: "#5b6478",
  edge: "#262c3d", edgeLit: "#3a4a66",
  void: "#0a0c12", case: "#12151f", case2: "#1a1f2e", panel2: "#0e111a",
};

export type Tier = "common" | "uncommon" | "rare" | "ultra";

export function tierOf(pct: number | null | undefined): Tier {
  if (pct == null) return "common";
  if (pct < 5) return "ultra";
  if (pct < 10) return "rare";
  if (pct < 30) return "uncommon";
  return "common";
}

export const tierColor = (t: Tier) =>
  ({ common: C.common, uncommon: C.uncommon, rare: C.rare, ultra: C.ua }[t]);

export const pctLabel = (pct: number | null | undefined) =>
  pct == null ? "—" : (pct < 5 ? pct.toFixed(1) : Math.round(pct)) + "%";

// Holographic text fill for ultra-rare names/chips.
export const HOLO: React.CSSProperties = {
  background: `linear-gradient(110deg,${C.ua} 0%,${C.ub} 30%,${C.uc} 55%,${C.ua} 80%,${C.ub} 100%)`,
  backgroundSize: "240% 100%",
  WebkitBackgroundClip: "text", backgroundClip: "text",
  WebkitTextFillColor: "transparent", color: "transparent", fontWeight: 700,
  animation: "foil 5.5s linear infinite",
};

export const STEAM_HEADER = (appid: number) =>
  `https://cdn.cloudflare.steamstatic.com/steam/apps/${appid}/header.jpg`;

export const IMG_FALLBACK =
  "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='56' height='56'%3E%3Crect width='56' height='56' fill='%231a1f2e'/%3E%3Cpath d='M28 17l9 11-9 11-9-11z' fill='none' stroke='%23394056' stroke-width='2'/%3E%3C/svg%3E";

export function onImgError(e: React.SyntheticEvent<HTMLImageElement>) {
  const el = e.currentTarget;
  if (el.dataset.fb) return;
  el.dataset.fb = "1";
  el.src = IMG_FALLBACK;
}
