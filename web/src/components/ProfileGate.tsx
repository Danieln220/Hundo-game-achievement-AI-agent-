import { useState, useRef, useEffect } from "react";
import { DEMO_PROFILE, health, saveAuthToken, session, sessionStatus, steamLoginUrl, withWake as apiWithWake } from "../api";
import type { SessionResult } from "../types";

const POLL_MS = 1500; // how often to poll /session/status while a snapshot builds
// Deadlines for a build that never finishes (23.3c): a killed worker used to
// leave the bar spinning forever. Give up after 15 min overall, or 3 min with
// the progress counter frozen — the build-lock TTL means a retry can take over.
const POLL_MAX_MS = 15 * 60 * 1000;
const POLL_STALL_MS = 3 * 60 * 1000;
const STUCK_MSG =
  "The profile build stopped making progress — it may have been interrupted. Please try again.";


// The landing page's sign-in controls: the demo is the primary action ("Ask it
// what to chase next"), Steam second, paste-a-profile third. The landing page
// supplies the hero copy around it.
export default function ProfileGate({
  onLoaded,
  demoOn,
  request,
}: {
  // `question` is only ever the one that came with `request` — see load().
  onLoaded: (session: SessionResult, question?: string) => void;
  // Whether the public demo profile is configured. The landing page owns the one
  // /health probe that answers this (it also warms the free-tier server).
  demoOn: boolean;
  // A parent can ask the gate to load a profile (the landing page's "Get the
  // guide" buttons enter the demo with a question). A new nonce = a new request.
  request?: { profile: string; nonce: number; question?: string };
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
    // The backend signs the id Steam verified; keep it so memory can be used
    // for this profile (23.4b). Typed-in ids get no token — memory stays off.
    const tok = params.get("auth");
    if (sid && tok) saveAuthToken(sid, tok);
    if (params.get("login_error")) setError("Steam sign-in failed — try again or enter your ID.");
    if (sid || params.get("login_error")) {
      window.history.replaceState({}, "", window.location.pathname);
    }
    if (sid) load(sid);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => { if (request) load(request.profile, request.question); // eslint-disable-line react-hooks/exhaustive-deps
  }, [request?.nonce]);

  const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

  // Cold-start retry now lives in api.ts (shared with Chat — 23.5d).
  const withWake = <T,>(fn: () => Promise<T>) =>
    apiWithWake(fn, setWaking, () => cancelled.current);

  // "Sign in through Steam" is a FULL-PAGE navigation to the API (it must be —
  // the API redirects to Steam). Navigating at a sleeping server would land on
  // Render's own error page, so we hold the click until /health answers.
  async function steamLogin(e: React.MouseEvent) {
    e.preventDefault();
    if (loading) return;
    cancelled.current = false;
    setLoading(true);
    setError(null);
    setProgress(null);
    try {
      const ok = await withWake(() => health());
      if (ok === undefined) return; // unmounted mid-wait
      window.location.href = steamLoginUrl(); // server is awake — off to Steam
    } catch (err) {
      setError((err as Error).message);
      setLoading(false);
      setWaking(false);
    }
  }

  // `question` belongs to THIS load only: it is handed back with this load's
  // result and dies with it. A request dropped because another load is running,
  // or a load that fails, can't leak its question into a later, different load
  // (code review 2026-09-24: the demo question was asked of the user's library).
  async function load(profile?: string, question?: string) {
    const p = (profile ?? value).trim();
    if (!p || loading) return;
    cancelled.current = false;
    setLoading(true);
    setError(null);
    setProgress(null);
    try {
      const r = await withWake(() => session(p));
      if (r === undefined) return; // unmounted mid-wait
      if (r.status === "ready") {
        onLoaded(r, question); // cached snapshot — straight in
        return;
      }
      // Building in the background — poll for live progress until ready/failed.
      // A transient poll failure (network blip, server cold start) must NOT kill
      // the flow — the build keeps running server-side. Give up only after 4 in a row.
      let misses = 0;
      const startedAt = Date.now();
      let lastDone = -1;
      let lastMovedAt = Date.now();
      for (;;) {
        if (cancelled.current) return;
        if (Date.now() - startedAt > POLL_MAX_MS || Date.now() - lastMovedAt > POLL_STALL_MS) {
          setError(STUCK_MSG);
          setLoading(false);
          setProgress(null);
          return;
        }
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
          onLoaded(st, question);
          return;
        }
        if (st.status === "failed") {
          setError(st.error);
          setLoading(false);
          setProgress(null);
          return;
        }
        if (st.progress.done !== lastDone) {
          lastDone = st.progress.done;
          lastMovedAt = Date.now();
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

  const pasteRow = (
    <div className="gate-row">
      <input
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={(e) => e.key === "Enter" && load()}
        placeholder="steamcommunity.com/id/yourname"
        disabled={loading}
      />
      <button onClick={() => load()} disabled={loading || !value.trim()}>
        {loading ? "Loading…" : "Load"}
      </button>
    </div>
  );
  // Valve's official sign-in art (brand guidelines ask for it; it's also the
  // button users already trust). Styled fallback text shows if the CDN fails.
  const steamButton = (
    <a className="steam-btn" href={steamLoginUrl()} onClick={steamLogin} aria-disabled={loading}>
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
  );

  return (
    <div className="gate gate-compact">
      {demoOn && (
        <button className="demo-btn demo-btn-primary" onClick={() => load(DEMO_PROFILE)} disabled={loading}>
          {loading ? "Loading the demo…" : "Ask it what to chase next"}
          <span>try it on a real library · no account needed</span>
        </button>
      )}
      {steamButton}
      <div className="gate-or"><span>or paste a profile</span></div>
      {pasteRow}

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
