import type { ChartSpec } from "../types";
import { C } from "../tcTheme";

const MONO = '"JetBrains Mono", monospace';

const fmt = (v: number) => (Number.isInteger(v) ? String(v) : v.toFixed(1));

/** Horizontal bar chart rendered from a backend chart SPEC (19.2).
 *  Hand-rolled divs on purpose: every Hundo chart is a top-N bar list, and this
 *  stays on-theme (gold fills, mono labels) with zero added dependencies. */
export default function BarChart({ spec }: { spec: ChartSpec }) {
  const items = spec.items ?? [];
  if (!items.length) return null;
  const max = Math.max(...items.map((i) => i.value), 0.0001);
  return (
    <div
      style={{
        margin: "14px 0 4px",
        border: `1px solid ${C.edge}`,
        borderRadius: 12,
        padding: "14px 16px 10px",
        background: C.panel2,
      }}
    >
      <div
        style={{
          fontFamily: MONO,
          fontSize: 11,
          letterSpacing: 1.2,
          textTransform: "uppercase",
          color: C.inkDim,
          marginBottom: 10,
        }}
      >
        📊 {spec.title || "Chart"}
        {spec.x_label ? ` · ${spec.x_label}` : ""}
      </div>
      {items.map((it, i) => (
        <div
          key={i}
          style={{ display: "flex", alignItems: "center", gap: 10, margin: "6px 0" }}
        >
          <span
            title={it.label}
            style={{
              flex: "0 0 170px",
              fontSize: 12.5,
              color: C.ink,
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
              textAlign: "right",
            }}
          >
            {it.label}
          </span>
          <div
            style={{
              flex: 1,
              height: 10,
              borderRadius: 5,
              background: C.case2,
              overflow: "hidden",
            }}
          >
            <div
              style={{
                width: `${Math.max((it.value / max) * 100, 1.5)}%`,
                height: "100%",
                borderRadius: 5,
                background: `linear-gradient(90deg, ${C.goldLo}, ${C.gold})`,
              }}
            />
          </div>
          <span
            style={{ flex: "0 0 52px", fontFamily: MONO, fontSize: 11.5, color: C.gold }}
          >
            {fmt(it.value)}
          </span>
        </div>
      ))}
    </div>
  );
}
