import { useState } from "react";
import { session } from "../api";

export default function ProfileGate({
  onLoaded,
}: {
  onLoaded: (steamId: string, games: number) => void;
}) {
  const [value, setValue] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function load() {
    if (!value.trim() || loading) return;
    setLoading(true);
    setError(null);
    try {
      const r = await session(value.trim());
      onLoaded(r.steam_id, r.games);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="gate">
      <h2>Analyze your Steam achievements</h2>
      <p className="muted">
        Enter your Steam alias, ID, or profile link. Your profile's game details
        must be <b>Public</b>.
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
        <p className="muted">Fetching your library (first time only, ~15–30s)…</p>
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
