import type { AuditData } from "../types";
import { RarityChip, rarityTier } from "../rarity";
import CompletionRing from "./CompletionRing";

const r = (n?: number | null) => Math.round(n ?? 0);

export default function AuditDashboard({
  data,
  onAction,
}: {
  data: AuditData;
  onAction?: (question: string) => void;
}) {
  return (
    <div className="audit">
      {data.intro && <p className="audit-intro">{data.intro}</p>}

      <div className="stat-tiles">
        <div className="tile ring-tile">
          <CompletionRing pct={data.overall_pct ?? 0} size={72} label="overall" />
        </div>
        <div className="tile">
          <div className="tile-num">{(data.total_unlocked ?? 0).toLocaleString()}</div>
          <div className="tile-label">unlocked</div>
        </div>
        <div className="tile">
          <div className="tile-num">{data.games_completed ?? 0}</div>
          <div className="tile-label">perfect games</div>
        </div>
        <div className="tile">
          <div className="tile-num">
            {data.games_started ?? 0}/{data.games_total ?? 0}
          </div>
          <div className="tile-label">started</div>
        </div>
      </div>

      {data.rarest?.name && (
        <div
          className={`flex-card ${
            rarityTier(data.rarest.rarity_pct) === "ultra" ? "holo-chip" : ""
          }`}
        >
          🏆{" "}
          <span className={rarityTier(data.rarest.rarity_pct) === "ultra" ? "holo" : ""}>
            <b>{data.rarest.name}</b>
          </span>{" "}
          <RarityChip pct={data.rarest.rarity_pct} /> — {data.rarest.game}
        </div>
      )}
      {data.focus?.game && (
        <div className="audit-row">
          🎯 <b>Focus:</b> {data.focus.game} — {data.focus.remaining} left ({r(data.focus.pct)}%)
          {onAction && (
            <button
              className="qa-chip"
              onClick={() => onAction(`Build me a roadmap to 100% ${data.focus!.game}`)}
            >
              🗺️ Build roadmap
            </button>
          )}
        </div>
      )}
      {data.momentum?.last_unlock && (
        <div className="audit-row">
          📈 <b>Momentum:</b> last unlock {data.momentum.last_unlock} ·{" "}
          {data.momentum.unlocks_last_30d ?? 0} in 30 days
        </div>
      )}

      {!!data.easy_wins?.length && (
        <div className="audit-section">
          <b>🟢 Easy wins</b>
          <ul>
            {data.easy_wins.map((a, i) => (
              <li key={i}>
                {a.name} ({r(a.rarity_pct)}%) — {a.game}
                {onAction && (
                  <button
                    className="qa-chip qa-inline"
                    onClick={() => onAction(`How do I unlock "${a.name}" in ${a.game}?`)}
                  >
                    How?
                  </button>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
      {!!data.abandoned?.length && (
        <div className="audit-section">
          <b>💤 Stalled games</b>
          <ul>
            {data.abandoned.map((a, i) => (
              <li key={i}>
                {a.game} — {r(a.pct)}% ({a.remaining} left)
                {onAction && (
                  <button
                    className="qa-chip qa-inline"
                    onClick={() => onAction(`Build me a roadmap to 100% ${a.game}`)}
                  >
                    🗺️ Roadmap
                  </button>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
