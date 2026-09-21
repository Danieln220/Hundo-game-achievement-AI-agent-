import type {
  AskResult,
  ChartSpec,
  LibraryData,
  SessionResponse,
  SessionStatus,
  Turn,
} from "./types";

// Strip any trailing slash so `BASE + "/session"` never becomes "…//session"
// (a double slash 404s on FastAPI).
const BASE = ((import.meta.env.VITE_API_URL as string) || "http://localhost:8000").replace(/\/+$/, "");

// Error that keeps the HTTP status, so callers can tell "server said no"
// (404 profile, 429 rate limit) apart from "server isn't up yet".
export class ApiError extends Error {
  status?: number;
  constructor(message: string, status?: number) {
    super(message);
    this.status = status;
  }
}

// True for failures that look like the free-tier host waking from idle: a
// network-level fetch rejection (browsers throw TypeError — the raw "Failed to
// fetch") or the edge's 502/503/504 while the server boots. These are worth
// retrying quietly instead of surfacing a scary error.
export const isWaking = (e: unknown): boolean =>
  e instanceof TypeError ||
  (e instanceof ApiError && [502, 503, 504].includes(e.status ?? 0));

// Retry schedule while the free-tier host boots (~85s total), then give up.
const WAKE_DELAYS_MS = [3000, 5000, 8000, 10000, 10000, 10000, 10000, 10000, 10000, 10000];
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

// Run `fn`, riding out a cold start. A browser TypeError is ALSO what a genuinely
// dropped connection looks like, so before claiming "waking up" we probe /health
// once: if the server answers, the failure was real and is surfaced immediately
// instead of being retried for 85 seconds behind a misleading message (23.5d).
// `onWaking(true|false)` drives the caller's "waking the server" UI.
export async function withWake<T>(
  fn: () => Promise<T>,
  onWaking?: (waking: boolean) => void,
  isCancelled?: () => boolean,
): Promise<T | undefined> {
  for (let attempt = 0; ; attempt++) {
    try {
      const out = await fn();
      onWaking?.(false);
      return out;
    } catch (e) {
      if (e instanceof DOMException && e.name === "AbortError") throw e;
      let waking = isWaking(e);
      if (waking && e instanceof TypeError) {
        // The server answering /health means the network is fine → real error.
        waking = await health().then(() => false).catch(() => true);
      }
      if (!waking || attempt >= WAKE_DELAYS_MS.length) {
        onWaking?.(false);
        if (waking) {
          throw new ApiError(
            "The server is taking unusually long to wake up — wait a minute and try again.",
          );
        }
        throw e;
      }
      onWaking?.(true);
      await sleep(WAKE_DELAYS_MS[attempt]);
      if (isCancelled?.()) return undefined;
    }
  }
}

// Turn a failed Response into an ApiError carrying the server's own message.
// The backend always sends a readable string `detail` (429 "Rate limit reached
// (15 per min). Try again in ~42s.", 404 unknown profile, 400 bad input), so a
// bare "Request failed (429)" is always a regression — the streaming path used
// to do exactly that (23.5b).
export async function apiError(res: Response): Promise<ApiError> {
  let detail = `Request failed (${res.status})`;
  try {
    const j = await res.json();
    if (typeof j?.detail === "string") detail = j.detail;
  } catch {
    /* non-JSON error body */
  }
  const retry = res.headers.get("retry-after");
  if (res.status === 429 && retry && !detail.includes(retry)) {
    detail += ` (retry in ${retry}s)`;
  }
  return new ApiError(detail, res.status);
}

async function post<T>(path: string, body: unknown, signal?: AbortSignal): Promise<T> {
  const res = await fetch(BASE + path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });
  if (!res.ok) throw await apiError(res);
  return res.json() as Promise<T>;
}

async function get<T>(path: string): Promise<T> {
  const res = await fetch(BASE + path);
  if (!res.ok) throw await apiError(res);
  return res.json() as Promise<T>;
}

// Cheap liveness probe — used to warm/await the free-tier server. The Steam
// sign-in is a full-page navigation to the API, so a sleeping server would show
// Render's own error page; we only navigate once /health answers.
export const health = () => get<{ status: string }>("/health");

// Returns a ready summary immediately if the snapshot exists, else {status:"building"}.
export const session = (profile: string) =>
  post<SessionResponse>("/session", { profile });

// Poll while a snapshot builds (Step 15.4): building (+progress) | ready (+summary) | failed.
export const sessionStatus = (steamId: string) =>
  get<SessionStatus>(`/session/status?steam_id=${encodeURIComponent(steamId)}`);

// Force a rebuild of an existing snapshot (23.3b). The current snapshot keeps
// serving while the new one builds, so the UI never goes empty.
export const refreshSession = (steamId: string) =>
  post<{ status: "refreshing" | "already_building"; steam_id: string }>(
    "/session/refresh",
    { steam_id: steamId },
  );

// "Sign in through Steam" (OpenID) — top-level navigation to the backend, which
// redirects to Steam and bounces back to the app with ?steam_id=.
export const steamLoginUrl = () => `${BASE}/auth/steam/login`;

// Full trophy-case dataset for a built profile (Step 17).
export const getLibrary = (steamId: string) =>
  get<LibraryData>(`/library?steam_id=${encodeURIComponent(steamId)}`);

// Currently most-played Steam games the user does NOT own (discovery).
export const getPopular = (steamId: string) =>
  get<{ games: { appid: number; name: string }[] }>(`/popular?steam_id=${encodeURIComponent(steamId)}`);

// Cross-session memory (Tier 2): what the agent remembers + a clear control.
export const getMemory = (steamId: string) =>
  get<{ memory: string }>(`/memory?steam_id=${encodeURIComponent(steamId)}`);

export async function clearMemory(steamId: string): Promise<void> {
  await fetch(`${BASE}/memory?steam_id=${encodeURIComponent(steamId)}`, { method: "DELETE" });
}

export const ask = (question: string, steam_id: string, history: Turn[]) =>
  post<AskResult>("/ask", { question, steam_id, history, with_insight: true });

export const chart = (result: AskResult, signal?: AbortSignal) =>
  post<{ chart_spec: ChartSpec | null; chart_url: string | null }>("/chart", { result }, signal);

// Streaming ask: reads the SSE stream from a POST (EventSource is GET-only, so we
// parse the text/event-stream manually). Calls onProgress(node) as nodes fire,
// returns the final AskResult. Pass an AbortSignal to cancel (the client stops
// reading; the agent still finishes server-side — true server cancel is future work).
export async function askStream(
  question: string,
  steam_id: string,
  history: Turn[],
  onProgress: (node: string) => void,
  signal?: AbortSignal,
  onToken?: (text: string) => void
): Promise<AskResult> {
  const res = await fetch(BASE + "/ask/stream", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question, steam_id, history, with_insight: true }),
    signal,
  });
  // Keep the server's message (429 detail, 404, 400) instead of a bare status —
  // this path used to throw "Stream failed (429)" and lose it (23.5b).
  if (!res.ok) throw await apiError(res);
  if (!res.body) throw new ApiError("The server sent an empty response.", res.status);

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let result: AskResult = {};
  let gotResult = false;

  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    // SSE frames are separated by a blank line.
    const frames = buffer.split("\n\n");
    buffer = frames.pop() ?? "";
    for (const frame of frames) {
      let event = "message";
      let data = "";
      for (const line of frame.split("\n")) {
        if (line.startsWith("event:")) event = line.slice(6).trim();
        // Per the SSE spec, multiple data: lines in one frame join with "\n"
        // (concatenating them corrupts any payload that spans lines).
        else if (line.startsWith("data:")) data += (data ? "\n" : "") + line.slice(5).trim();
      }
      if (!data) continue;
      // One malformed frame must not kill a stream whose answer is otherwise
      // fine — skip it and keep reading (23.5f).
      let parsed: { node?: string; text?: string } & AskResult;
      try {
        parsed = JSON.parse(data);
      } catch {
        continue;
      }
      if (event === "progress") onProgress(parsed.node ?? "");
      else if (event === "token") onToken?.(parsed.text ?? "");
      else if (event === "result") {
        result = parsed;
        gotResult = true;
      }
    }
  }
  // A stream that ends without a `result` event produced NOTHING. Returning {}
  // here used to add a blank assistant turn with no Retry button, which then fed
  // the agent an empty answer as context on the next question (23.5b).
  if (!gotResult) {
    throw new ApiError("The answer didn't come through — please try again.");
  }
  return result;
}

// Resolve a chart_url for <img>. In prod the backend returns an ABSOLUTE Supabase
// URL (use as-is); in local dev it's a relative /charts/x.png (prefix the API origin).
export const assetUrl = (url: string) =>
  /^https?:\/\//.test(url) ? url : BASE + url;
