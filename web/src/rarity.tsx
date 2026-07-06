// The rarity system — the soul of the design. The thresholds live in ONE place
// (tcTheme.tierOf) so a 12% achievement is the same tier/color in a chat
// roadmap card as it is in the trophy case. This module just renders the chip.

import { tierOf, pctLabel, type Tier } from "./tcTheme";

export type RarityTier = Tier | "unknown";

export function rarityTier(pct?: number | string | null): RarityTier {
  if (pct == null || Number.isNaN(Number(pct))) return "unknown";
  return tierOf(pct);
}

export function RarityChip({ pct }: { pct?: number | string | null }) {
  const tier = rarityTier(pct);
  const label = pctLabel(pct);

  if (tier === "ultra") {
    return (
      <span className="rarity-chip holo-chip">
        <span className="holo">{label}</span>
      </span>
    );
  }
  return <span className={`rarity-chip r-${tier}`}>{label}</span>;
}
