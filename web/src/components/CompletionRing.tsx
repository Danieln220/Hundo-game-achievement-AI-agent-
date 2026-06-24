// SVG completion ring — a "trophy case" read of a percentage. Brass-gold arc on a
// case-colored track, with the number in the middle. Used in the profile header
// and the audit overall stat.

export default function CompletionRing({
  pct,
  size = 64,
  stroke = 6,
  label,
}: {
  pct: number;
  size?: number;
  stroke?: number;
  label?: string;
}) {
  const clamped = Math.max(0, Math.min(100, pct));
  const r = (size - stroke) / 2;
  const c = 2 * Math.PI * r;
  const offset = c * (1 - clamped / 100);
  const gid = `ring-${size}`;

  return (
    <div className="ring" style={{ width: size, height: size }}>
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`}>
        <defs>
          <linearGradient id={gid} x1="0" y1="0" x2="1" y2="1">
            <stop offset="0%" stopColor="var(--gold-lo)" />
            <stop offset="100%" stopColor="var(--gold)" />
          </linearGradient>
        </defs>
        <circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          fill="none"
          stroke="var(--case-2)"
          strokeWidth={stroke}
        />
        <circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          fill="none"
          stroke={`url(#${gid})`}
          strokeWidth={stroke}
          strokeLinecap="round"
          strokeDasharray={c}
          strokeDashoffset={offset}
          transform={`rotate(-90 ${size / 2} ${size / 2})`}
          style={{ transition: "stroke-dashoffset 0.7s cubic-bezier(0.2,0.8,0.2,1)" }}
        />
      </svg>
      <div className="ring-text">
        <span className="ring-pct">{Math.round(clamped)}%</span>
        {label && <span className="ring-label">{label}</span>}
      </div>
    </div>
  );
}
