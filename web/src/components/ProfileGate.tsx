import { useState, useRef, useEffect } from "react";
import { isWaking, session, sessionStatus, steamLoginUrl } from "../api";
import type { SessionResult } from "../types";

const POLL_MS = 1500; // how often to poll /session/status while a snapshot builds

// Retry schedule for a cold-starting server (Render free tier naps when idle and
// takes 30–90s to boot; its edge answers 502 meanwhile — the browser shows that
// as a bare "Failed to fetch" without this). ~85s total, then we give up.
const WAKE_DELAYS_MS = [3000, 5000, 8000, 10000, 10000, 10000, 10000, 10000, 10000, 10000];

export default function ProfileGate({
  onLoaded,
}: {
  onLoaded: (session: SessionResult) => void;
}) {
  const [value, setValue] = useState("");
  const [loading, setLoading] = useState(false);
  const [waking, setWaking] = useState(false); // server cold-starting — retrying quietly
  const [error, setError] = useState<string | null>(null);
  // Real build progress (done/total/pct) from the server; null until the first
  // progress tick (or for the fast cached path, which never shows a bar).
  const [progress, setProgress] = useState<{ done: number; total: number; pct: number } | null>(null);

  // Stop polling if the component unmounts mid-build.
  const cancelled = useRef(false);
  useEffect(() => () => { cancelled.current = true; }, []);

  // Returning from "Sign in through Steam": the backend bounced us back with a
  // verified ?steam_id= (or ?login_error=1). Auto-load it, then clean the URL.
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const sid = params.get("steam_id");
    if (params.get("login_error")) setError("Steam sign-in failed — try again or enter your ID.");
    if (sid || params.get("login_error")) {
      window.history.replaceState({}, "", window.location.pathname);
    }
    if (sid) load(sid);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

  async function load(profile?: string) {
    const p = (profile ?? value).trim();
    if (!p || loading) return;
    cancelled.current = false;
    setLoading(true);
    setError(null);
    setProgress(null);
    try {
      // Connect, riding out a cold start: on a wake-shaped failure keep retrying
      // (with a visible "waking the server" state) instead of surfacing the
      // browser's raw "Failed to fetch". Real refusals (404 unknown profile,
      // 429 rate limit) surface immediately.
      let r;
      for (let attempt = 0; ; attempt++) {
        try {
          r = await session(p);
          break;
        } catch (e) {
          if (!isWaking(e) || attempt >= WAKE_DELAYS_MS.length) {
            if (isWaking(e)) {
              throw new Error(
                "The server is taking unusually long to wake up — wait a minute and try again."
              );
            }
            throw e;
          }
          setWaking(true);
          await sleep(WAKE_DELAYS_MS[attempt]);
          if (cancelled.current) return;
        }
      }
      setWaking(false);
      if (r.status === "ready") {
        onLoaded(r); // cached snapshot — straight in
        return;
      }
      // Building in the background — poll for live progress until ready/failed.
      // A transient poll failure (network blip, server cold start) must NOT kill
      // the flow — the build keeps running server-side. Give up only after 4 in a row.
      let misses = 0;
      for (;;) {
        if (cancelled.current) return;
        await sleep(POLL_MS);
        let st;
        try {
          st = await sessionStatus(r.steam_id);
          misses = 0;
        } catch (pollErr) {
          if (++misses >= 4) throw pollErr;
          continue;
        }
        if (cancelled.current) return;
        if (st.status === "ready") {
          onLoaded(st);
          return;
        }
        if (st.status === "failed") {
          setError(st.error);
          setLoading(false);
          setProgress(null);
          return;
        }
        setProgress(st.progress);
      }
    } catch (e) {
      setError((e as Error).message);
      setLoading(false);
      setWaking(false);
      setProgress(null);
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
        <button onClick={() => load()} disabled={loading || !value.trim()}>
          {loading ? "Loading…" : "Load my data"}
        </button>
      </div>

      <div className="gate-or"><span>or</span></div>

      {/* Valve's official sign-in art (brand guidelines ask for it; it's also the
          button users already trust). Styled fallback text shows if the CDN fails. */}
      <a className="steam-btn" href={steamLoginUrl()} aria-disabled={loading}>
        <img
          src="https://community.cloudflare.steamstatic.com/public/images/signinthroughsteam/sits_01.png"
          alt="Sign in through Steam"
          height={35}
          onError={(e) => {
            e.currentTarget.style.display = "none";
            e.currentTarget.insertAdjacentText("beforebegin", "Sign in through Steam");
          }}
        />
      </a>

      {loading && (
        <div className="gate-progress">
          {waking ? (
            <>
              <div className="indeterminate-bar">
                <div className="indeterminate-fill" />
              </div>
              <p className="muted small">
                Waking the server — free hosting naps when idle.{" "}
                <span className="muted">This can take up to a minute…</span>
              </p>
            </>
          ) : progress && progress.total > 0 ? (
            <>
              <div className="progress-bar">
                <div style={{ width: `${progress.pct}%` }} />
              </div>
              <p className="muted small">
                Building your trophy case… {progress.pct}%{" "}
                <span className="muted">({progress.done}/{progress.total} fetched)</span>
              </p>
            </>
          ) : (
            <>
              <div className="indeterminate-bar">
                <div className="indeterminate-fill" />
              </div>
              <p className="muted small">
                Fetching your library… <span className="muted">(first time only)</span>
              </p>
            </>
          )}
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
