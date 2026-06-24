// The rarity system — the soul of the design. Maps a global unlock % to a tier,
// and renders a chip. Ultra-rare (<5%) gets the holographic foil signature.

export type RarityTier = "common" | "uncommon" | "rare" | "ultra" | "unknown";

export function rarityTier(pct?: number | null): RarityTier {
  if (pct == null || Number.isNaN(pct)) return "unknown";
  if (pct >= 50) return "common";
  if (pct >= 15) return "uncommon";
  if (pct >= 5) return "rare";
  return "ultra";
}

export function RarityChip({ pct }: { pct?: number | null }) {
  const tier = rarityTier(pct);
  const label = pct == null ? "—" : `${Math.round(pct)}%`;

  if (tier === "ultra") {
    return (
      <span className="rarity-chip holo-chip">
        <span className="holo">{label}</span>
      </span>
    );
  }
  return <span className={`rarity-chip r-${tier}`}>{label}</span>;
}
