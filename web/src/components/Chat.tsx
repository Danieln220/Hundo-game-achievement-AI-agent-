import { useState, useRef, useEffect } from "react";
import { ask, chart } from "../api";
import type { AskResult, Turn } from "../types";
import Message from "./Message";

interface UserMsg {
  role: "user";
  text: string;
}
interface AssistantMsg {
  role: "assistant";
  result: AskResult;
  chartUrl?: string | null;
  chartLoading?: boolean;
}
type Msg = UserMsg | AssistantMsg;

const EXAMPLES = [
  "Which games am I closest to finishing?",
  "What are my top 3 rarest achievements?",
  "Build me a roadmap to 100% Rocket League",
  "Give me a full audit of my profile",
];

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
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, busy]);

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

  async function send(q: string) {
    if (!q.trim() || busy) return;
    const history = buildHistory(messages);
    const assistantIndex = messages.length + 1; // user pushed, then assistant

    setMessages((m) => [...m, { role: "user", text: q }]);
    setInput("");
    setBusy(true);

    try {
      const result = await ask(q, steamId, history);
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
          const c = await chart(result);
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
      setMessages((m) => [
        ...m,
        { role: "assistant", result: { answer: `⚠️ ${(e as Error).message}` } },
      ]);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="chat">
      <div className="messages">
        {messages.length === 0 && (
          <div className="welcome">
            <p className="muted">Loaded {games} games. Ask me anything — or try:</p>
            <div className="examples">
              {EXAMPLES.map((ex) => (
                <button key={ex} className="example" onClick={() => send(ex)}>
                  {ex}
                </button>
              ))}
            </div>
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
            />
          )
        )}

        {busy && <div className="thinking">Thinking…</div>}
        <div ref={endRef} />
      </div>

      <div className="composer">
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && send(input)}
          placeholder="Ask about your achievements…"
          disabled={busy}
        />
        <button onClick={() => send(input)} disabled={busy || !input.trim()}>
          Send
        </button>
      </div>
    </div>
  );
}
