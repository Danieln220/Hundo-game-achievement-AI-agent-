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
}: {
  steamId: string;
  games: number;
}) {
  const [messages, setMessages] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState<string[]>([]);
  const endRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, busy, progress]);

  // Refocus the composer once a request settles, so you can keep typing without
  // clicking back into the box after every send.
  useEffect(() => {
    if (!busy) inputRef.current?.focus();
  }, [busy]);

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

  async function send(q: string) {
    if (!q.trim() || busy) return;
    const history = buildHistory(messages);
    const assistantIndex = messages.length + 1; // user pushed, then assistant
    const controller = new AbortController();
    abortRef.current = controller;

    setMessages((m) => [...m, { role: "user", text: q }]);
    setInput("");
    setBusy(true);
    setProgress([]);

    try {
      const result = await askStream(
        q,
        steamId,
        history,
        (node) => setProgress((p) => [...p, node]),
        controller.signal
      );
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
      abortRef.current = null;
    }
  }

  return (
    <div className="chat">
      <div className="messages">
        {messages.length === 0 && (
          <div className="welcome">
            <p className="muted">Loaded {games} games. Ask me anything — or try:</p>
            <SuggestionChips onPick={send} />
          </div>
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

        {busy && <ProgressSteps nodes={progress} />}
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
          placeholder="Ask about your achievements…  (Shift+Enter for a new line)"
          rows={1}
          disabled={busy}
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
