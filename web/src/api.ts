import type {
  AskResult,
  SessionResponse,
  SessionStatus,
  Turn,
} from "./types";

// Strip any trailing slash so `BASE + "/session"` never becomes "…//session"
// (a double slash 404s on FastAPI).
const BASE = ((import.meta.env.VITE_API_URL as string) || "http://localhost:8000").replace(/\/+$/, "");

async function post<T>(path: string, body: unknown, signal?: AbortSignal): Promise<T> {
  const res = await fetch(BASE + path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });
  if (!res.ok) {
    let detail = `Request failed (${res.status})`;
    try {
      const j = await res.json();
      if (j?.detail) detail = j.detail;
    } catch {
      /* non-JSON error body */
    }
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

async function get<T>(path: string): Promise<T> {
  const res = await fetch(BASE + path);
  if (!res.ok) {
    let detail = `Request failed (${res.status})`;
    try {
      const j = await res.json();
      if (j?.detail) detail = j.detail;
    } catch {
      /* non-JSON error body */
    }
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

// Returns a ready summary immediately if the snapshot exists, else {status:"building"}.
export const session = (profile: string) =>
  post<SessionResponse>("/session", { profile });

// Poll while a snapshot builds (Step 15.4): building (+progress) | ready (+summary) | failed.
export const sessionStatus = (steamId: string) =>
  get<SessionStatus>(`/session/status?steam_id=${encodeURIComponent(steamId)}`);

export const ask = (question: string, steam_id: string, history: Turn[]) =>
  post<AskResult>("/ask", { question, steam_id, history, with_insight: true });

export const chart = (result: AskResult, signal?: AbortSignal) =>
  post<{ chart_url: string | null }>("/chart", { result }, signal);

// Streaming ask: reads the SSE stream from a POST (EventSource is GET-only, so we
// parse the text/event-stream manually). Calls onProgress(node) as nodes fire,
// returns the final AskResult. Pass an AbortSignal to cancel (the client stops
// reading; the agent still finishes server-side — true server cancel is future work).
export async function askStream(
  question: string,
  steam_id: string,
  history: Turn[],
  onProgress: (node: string) => void,
  signal?: AbortSignal
): Promise<AskResult> {
  const res = await fetch(BASE + "/ask/stream", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question, steam_id, history, with_insight: true }),
    signal,
  });
  if (!res.ok || !res.body) throw new Error(`Stream failed (${res.status})`);

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let result: AskResult = {};

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
        else if (line.startsWith("data:")) data += line.slice(5).trim();
      }
      if (!data) continue;
      const parsed = JSON.parse(data);
      if (event === "progress") onProgress(parsed.node);
      else if (event === "result") result = parsed;
    }
  }
  return result;
}

// Resolve a chart_url for <img>. In prod the backend returns an ABSOLUTE Supabase
// URL (use as-is); in local dev it's a relative /charts/x.png (prefix the API origin).
export const assetUrl = (url: string) =>
  /^https?:\/\//.test(url) ? url : BASE + url;
