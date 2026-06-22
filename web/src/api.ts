import type { AskResult, SessionResult, Turn } from "./types";

const BASE = (import.meta.env.VITE_API_URL as string) || "http://localhost:8000";

async function post<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(BASE + path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
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

export const session = (profile: string) =>
  post<SessionResult>("/session", { profile });

export const ask = (question: string, steam_id: string, history: Turn[]) =>
  post<AskResult>("/ask", { question, steam_id, history, with_insight: true });

export const chart = (result: AskResult) =>
  post<{ chart_url: string | null }>("/chart", { result });

// Prefix a relative chart_url (e.g. /charts/x.png) with the API origin for <img>.
export const assetUrl = (url: string) => BASE + url;
