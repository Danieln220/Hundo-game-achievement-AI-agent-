import { useState, useRef, useEffect } from "react";
import { askStream, chart } from "../api";
import type { AskResult, Turn } from "../types";
import Message from "./Message";
import ProgressSteps from "./ProgressSteps";
import SuggestionChips from "./SuggestionChips";
import { followupsFor } from "../followups";

interface UserMsg {
  role: "user";
  text: string;
}
interface AssistantMsg {
  role: "assistant";
  result: AskResult;
  chartUrl?: string | null;
  chartLoading?: boolean;
  retryQuestion?: string; // set on error/stop so the message can offer a retry
}
type Msg = UserMsg | AssistantMsg;

export default function Chat({
  steamId,
  games,
  inject,
  greeting,
  starters,
}: {
  steamId: string;
  games: number;
  // External question to auto-send (e.g. from the trophy case). Bump `nonce` to fire.
  inject?: { q: string; nonce: number };
  // Optional opening line + a minimal set of starter chips (curator drawer). When
  // omitted, falls back to the full categorized SuggestionChips. A chip with `q`
  // sends immediately; a chip with `fill` PREFILLS the composer (a prebuilt
  // question the user completes, e.g. by adding a game name) and focuses it.
  greeting?: string;
  starters?: { label: string; q?: string; fill?: string }[];
}) {
  const [messages, setMessages] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState<string[]>([]);
  const [streamingText, setStreamingText] = useState(""); // live answer tokens
  const endRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  // A follow-up typed while the agent is busy is queued here and auto-sent when
  // the current answer finishes (one request at a time).
  const pendingRef = useRef<string | null>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, busy, progress, streamingText]);

  // When a request settles: flush any queued follow-up, else refocus the composer
  // so you can keep typing without clicking back into the box.
  useEffect(() => {
    if (busy) return;
    if (pendingRef.current) {
      const q = pendingRef.current;
      pendingRef.current = null;
      send(q);
    } else {
      inputRef.current?.focus();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [busy]);

  // Auto-send an externally injected question (trophy-case actions).
  useEffect(() => {
    if (inject?.q) send(inject.q);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [inject?.nonce]);

  // Auto-grow the textarea up to a cap as the question spans multiple lines.
  useEffect(() => {
    const el = inputRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 160)}px`;
  }, [input]);

  function buildHistory(msgs: Msg[]): Turn[] {
    const turns: Turn[] = [];
    for (let i = 0; i < msgs.length; i++) {
      const m = msgs[i];
      const next = msgs[i + 1];
      if (m.role === "user" && next?.role === "assistant") {
        turns.push({ question: m.text, answer: next.result.answer || "" });
      }
    }
    return turns;
  }

  function stop() {
    abortRef.current?.abort();
  }

  // Prefill the composer with a prebuilt question and focus it (cursor at the end,
  // so the user just types the game/name to complete it). Does NOT send.
  function prefill(text: string) {
    setInput(text);
    setTimeout(() => {
      const el = inputRef.current;
      if (el) { el.focus(); el.setSelectionRange(text.length, text.length); }
    }, 0);
  }

  async function send(q: string) {
    if (!q.trim()) return;
    if (busy) {
      // Queue the follow-up; the busy effect sends it once the current finishes.
      pendingRef.current = q;
      setInput("");
      return;
    }
    const history = buildHistory(messages);
    const assistantIndex = messages.length + 1; // user pushed, then assistant
    const controller = new AbortController();
    abortRef.current = controller;

    setMessages((m) => [...m, { role: "user", text: q }]);
    setInput("");
    setBusy(true);
    setProgress([]);
    setStreamingText("");

    try {
      const result = await askStream(
        q,
        steamId,
        history,
        (node) => setProgress((p) => [...p, node]),
        controller.signal,
        (tok) => setStreamingText((s) => s + tok)
      );
      setStreamingText("");
      setMessages((m) => [
        ...m,
        {
          role: "assistant",
          result,
          chartUrl: result.chart_url,
          chartLoading: !!result.chart_pending && !result.chart_url,
        },
      ]);

      // Answer-first: fetch the chart in a second pass if one is pending.
      if (result.chart_pending && !result.chart_url) {
        try {
          const c = await chart(result, controller.signal);
          setMessages((m) =>
            m.map((msg, i) =>
              i === assistantIndex && msg.role === "assistant"
                ? { ...msg, chartUrl: c.chart_url, chartLoading: false }
                : msg
            )
          );
        } catch {
          setMessages((m) =>
            m.map((msg, i) =>
              i === assistantIndex && msg.role === "assistant"
                ? { ...msg, chartLoading: false }
                : msg
            )
          );
        }
      }
    } catch (e) {
      const err = e as Error;
      const stopped = err.name === "AbortError";
      setMessages((m) => [
        ...m,
        {
          role: "assistant",
          result: { answer: stopped ? "⏹ Stopped." : `⚠️ ${err.message}` },
          retryQuestion: q,
        },
      ]);
    } finally {
      setBusy(false);
      setStreamingText("");
      abortRef.current = null;
    }
  }

  return (
    <div className="chat">
      <div className="messages">
        {messages.length === 0 && (
          greeting ? (
            <div className="curator-welcome">
              <div className="curator-greet">{greeting}</div>
              <div className="curator-starters">
                {(starters ?? []).map((s) => (
                  <button key={s.label} className="example" onClick={() => (s.fill ? prefill(s.fill) : send(s.q!))}>{s.label}</button>
                ))}
              </div>
            </div>
          ) : (
            <div className="welcome">
              <p className="muted">Loaded {games} games. Ask me anything — or try:</p>
              <SuggestionChips onPick={send} />
            </div>
          )
        )}

        {messages.map((m, i) =>
          m.role === "user" ? (
            <Message key={i} role="user" text={m.text} />
          ) : (
            <Message
              key={i}
              role="assistant"
              result={m.result}
              chartUrl={m.chartUrl}
              chartLoading={m.chartLoading}
              onAction={send}
              retryQuestion={m.retryQuestion}
              onRetry={send}
            />
          )
        )}

        {busy && streamingText && (
          <div className="msg assistant">
            <div className="bubble">
              <div className="streaming">{streamingText}<span className="stream-caret" /></div>
            </div>
          </div>
        )}
        {busy && !streamingText && <ProgressSteps nodes={progress} />}
        <div ref={endRef} />
      </div>

      {(() => {
        if (busy || messages.length === 0) return null;
        const last = [...messages]
          .reverse()
          .find((m): m is AssistantMsg => m.role === "assistant");
        const chips = followupsFor(last?.result);
        if (!chips.length) return null;
        return (
          <div className="followups">
            {chips.map((c) => (
              <button key={c.q} className="example followup" onClick={() => send(c.q)}>
                {c.label}
              </button>
            ))}
          </div>
        );
      })()}

      <div className="composer">
        <textarea
          ref={inputRef}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              send(input);
            }
          }}
          placeholder={
            busy
              ? "Type your next question — it'll send when the current one finishes…"
              : "Ask about your achievements…  (Shift+Enter for a new line)"
          }
          rows={1}
        />
        {busy ? (
          <button className="stop-btn" onClick={stop}>
            ⏹ Stop
          </button>
        ) : (
          <button onClick={() => send(input)} disabled={!input.trim()}>
            Send
          </button>
        )}
      </div>
    </div>
  );
}
