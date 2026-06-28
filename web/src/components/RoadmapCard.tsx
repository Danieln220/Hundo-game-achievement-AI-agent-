import type { Achievement, RoadmapData } from "../types";
import { RarityChip } from "../rarity";

function Tier({
  title,
  cls,
  items,
  count,
  onAsk,
}: {
  title: string;
  cls: string;
  items: Achievement[];
  count: number;
  onAsk?: (name: string) => void;
}) {
  if (!items.length) return null;
  return (
    <div className={`tier ${cls}`}>
      <div className="tier-head">
        {title} <span className="tier-count">{count}</span>
      </div>
      <ul className="tier-list">
        {items.map((a, i) => (
          <li key={i}>
            {onAsk ? (
              <button
                className="ach-name ach-link"
                onClick={() => onAsk(a.name)}
                title="Get a guide for this achievement"
              >
                {a.name} <span className="ach-guide">↗ guide</span>
              </button>
            ) : (
              <span className="ach-name">{a.name}</span>
            )}
            <RarityChip pct={a.rarity_pct} />
            {a.description && !a.hidden && (
              <span className="ach-desc">{a.description}</span>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}

const REFINEMENTS = [
  { label: "Only the easy ones", q: "Show me only the easy ones" },
  { label: "Skip multiplayer", q: "Rebuild it but skip multiplayer achievements" },
  { label: "Skip DLC", q: "Rebuild it but skip DLC achievements" },
];

export default function RoadmapCard({
  data,
  onAction,
}: {
  data: RoadmapData;
  onAction?: (question: string) => void;
}) {
  // Clicking an achievement asks the curator for a real guide (agent + web search
  // → answer with numbered sources, rendered right in the drawer).
  const ask = onAction
    ? (name: string) => onAction(`How do I unlock "${name}" in ${data.target}?`)
    : undefined;
  return (
    <div className="roadmap">
      <div className="roadmap-head">
        <h3>🗺️ {data.target}</h3>
        <div className="muted small">
          {data.unlocked}/{data.total} unlocked · {data.remaining} to go
          {data.shown != null && data.shown < data.remaining && (
            <> · showing the {data.shown} easiest</>
          )}
        </div>
        <div className="progress-bar">
          <div style={{ width: `${data.pct_done}%` }} />
        </div>
      </div>

      <Tier title="🟢 Quick wins" cls="quick" items={data.tiers.quick} count={data.tier_counts.quick} onAsk={ask} />
      <Tier title="🟡 Moderate" cls="moderate" items={data.tiers.moderate} count={data.tier_counts.moderate} onAsk={ask} />
      <Tier title="🔴 Challenge / grind" cls="challenge" items={data.tiers.challenge} count={data.tier_counts.challenge} onAsk={ask} />

      {data.howto.length > 0 && (
        <div className="roadmap-howto">
          <b>Guides for the hardest:</b>
          <ul>
            {data.howto.map((h, i) => (
              <li key={i}>
                <a href={h.url} target="_blank" rel="noreferrer">
                  {h.name}
                </a>
              </li>
            ))}
          </ul>
        </div>
      )}

      {onAction ? (
        <div className="quick-actions">
          <span className="qa-label">Refine:</span>
          {REFINEMENTS.map((r) => (
            <button key={r.label} className="qa-chip" onClick={() => onAction(r.q)}>
              {r.label}
            </button>
          ))}
        </div>
      ) : (
        <p className="muted small">
          Refine it — try "only the easy ones", "skip multiplayer", or a different game.
        </p>
      )}
    </div>
  );
}
