import type { AskResult } from "../types";

export default function ReasoningTrace({ result }: { result: AskResult }) {
  const codes = result.code_history ?? [];
  return (
    <details className="trace">
      <summary>Reasoning trace</summary>

      {result.plan && (
        <>
          <h4>Plan</h4>
          <pre className="plan">{result.plan}</pre>
        </>
      )}

      {result.method && (
        <p className="muted small">
          {result.method === "deterministic"
            ? "Computed directly from your snapshot — locked achievements ranked by global rarity. No code generation needed, so nothing here can be invented."
            : result.method === "live_fetch"
            ? "Fetched live from Steam (you don't own this game) — every achievement is listed as locked."
            : "The agent wrote pandas for this and ran it in the sandbox (code below)."}
        </p>
      )}

      {result.interpretation && (
        <p className="muted small">Interpretation: {result.interpretation}</p>
      )}

      {codes.length > 0 && (
        <>
          <h4>Code attempts</h4>
          {codes.map((c, i) => (
            <pre key={i} className="code">
              {c}
            </pre>
          ))}
        </>
      )}

      <p className="muted small">
        Code attempts: {result.retries ?? 0}
        {result.last_error ? ` · last error: ${result.last_error}` : ""}
      </p>

      {result.last_result && (
        <>
          <h4>Raw result</h4>
          <pre className="code">{result.last_result}</pre>
        </>
      )}
    </details>
  );
}
