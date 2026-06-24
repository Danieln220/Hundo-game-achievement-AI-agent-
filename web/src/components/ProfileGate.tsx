import { useState, useEffect } from "react";
import { session } from "../api";
import type { SessionResult } from "../types";

// Staged labels shown during the (synchronous) snapshot build. These are
// time-driven, NOT real progress — /session blocks until done and exposes no
// percentage. A true progress bar needs the async snapshot build deferred to
// Step 15; until then this is an honest "here's roughly what's happening" cycle.
const STAGES = [
  "Resolving your profile…",
  "Fetching your library…",
  "Building your trophy case…",
  "Crunching achievement rarity…",
  "Almost there…",
];

export default function ProfileGate({
  onLoaded,
}: {
  onLoaded: (session: SessionResult) => void;
}) {
  const [value, setValue] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [stage, setStage] = useState(0);

  // Advance the stage label every ~6s while loading (caps at the last stage).
  useEffect(() => {
    if (!loading) {
      setStage(0);
      return;
    }
    const id = setInterval(
      () => setStage((s) => Math.min(s + 1, STAGES.length - 1)),
      6000
    );
    return () => clearInterval(id);
  }, [loading]);

  async function load() {
    if (!value.trim() || loading) return;
    setLoading(true);
    setError(null);
    try {
      const r = await session(value.trim());
      onLoaded(r);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="gate">
      <h2>Open your trophy case</h2>
      <p className="muted">
        Your achievements, read like rare cards — rarity, roadmaps, and a full
        profile audit. Enter your Steam alias, ID, or profile link to begin.
        Game details must be <b>Public</b>.
      </p>

      <div className="gate-row">
        <input
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && load()}
          placeholder="e.g. daniel"
          disabled={loading}
          autoFocus
        />
        <button onClick={load} disabled={loading || !value.trim()}>
          {loading ? "Loading…" : "Load my data"}
        </button>
      </div>

      {loading && (
        <div className="gate-progress">
          <div className="indeterminate-bar">
            <div className="indeterminate-fill" />
          </div>
          <p className="muted small">{STAGES[stage]} <span className="muted">(first time only, ~15–30s)</span></p>
        </div>
      )}
      {error && <p className="error">{error}</p>}

      <details className="hint">
        <summary>Where do I find this?</summary>
        <ul>
          <li>
            <b>Alias</b> = the custom name in your URL:{" "}
            steamcommunity.com/id/<b>daniel</b> → type <code>daniel</code>
          </li>
          <li>Or paste the full link (/id/… or /profiles/7656…) or your 17-digit SteamID.</li>
          <li>Your in-game display name can't be searched — use the alias from your URL.</li>
        </ul>
      </details>
    </div>
  );
}
