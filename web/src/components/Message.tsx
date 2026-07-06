import ReactMarkdown from "react-markdown";
import type { Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import { assetUrl } from "../api";
import type { AskResult } from "../types";
import RoadmapCard from "./RoadmapCard";
import AuditDashboard from "./AuditDashboard";
import ReasoningTrace from "./ReasoningTrace";

type Props =
  | { role: "user"; text: string }
  | {
      role: "assistant";
      result: AskResult;
      chartUrl?: string | null;
      chartLoading?: boolean;
      onAction?: (question: string) => void;
      retryQuestion?: string;
      onRetry?: (question: string) => void;
    };

// Open links in a new tab.
const mdComponents: Components = {
  a: ({ href, children }) => (
    <a href={href} target="_blank" rel="noreferrer">
      {children}
    </a>
  ),
};

// The how-to/time answers end with a free-form "Sources:" list the model writes
// (often raw, unnumbered URLs). When we have structured sources, drop that
// trailing block and render a clean numbered list instead.
function stripTrailingSources(md: string): string {
  return md.replace(/\n+\s*\**#{0,6}\s*sources\b\**\s*:?[\s\S]*$/i, "").trimEnd();
}

export default function Message(props: Props) {
  if (props.role === "user") {
    return (
      <div className="msg user">
        <div className="bubble">{props.text}</div>
      </div>
    );
  }

  const { result, chartUrl, chartLoading, onAction, retryQuestion, onRetry } = props;
  const hasSources = (result.sources?.length ?? 0) > 0;

  // Route-specific rich rendering; markdown is the fallback for everything else.
  const answerText = hasSources
    ? stripTrailingSources(result.answer || "")
    : result.answer || "(no answer)";
  const body = result.roadmap ? (
    <RoadmapCard data={result.roadmap} onAction={onAction} />
  ) : result.audit ? (
    <AuditDashboard data={result.audit} onAction={onAction} />
  ) : (
    <div className="markdown">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={mdComponents}>
        {answerText || "(no answer)"}
      </ReactMarkdown>
    </div>
  );

  return (
    <div className="msg assistant">
      <div className="bubble">
        {body}

        {retryQuestion && onRetry && (
          <button className="qa-chip retry-btn" onClick={() => onRetry(retryQuestion)}>
            ↻ Retry
          </button>
        )}

        {result.insight && <div className="insight">💡 {result.insight}</div>}

        {hasSources && (
          <div className="sources">
            <div className="sources-head">Sources</div>
            <ol>
              {result.sources!.map((s, i) => (
                <li key={i}>
                  <a href={s.url} target="_blank" rel="noreferrer">
                    {s.title || s.url}
                  </a>
                </li>
              ))}
            </ol>
          </div>
        )}

        {chartLoading && (
          <div className="chart-skeleton" aria-label="Generating chart">
            <span className="skeleton-label">📊 Generating chart…</span>
          </div>
        )}
        {chartUrl && (
          <img
            className="chart"
            src={assetUrl(chartUrl)}
            alt="chart"
            onError={(e) => { e.currentTarget.style.display = "none"; }}
          />
        )}

        {/* "Computed, not guessed" — the agent's plan + real code attempts,
            collapsed by default. Only shown when the answer was computed. */}
        {((result.code_history?.length ?? 0) > 0 || result.plan) && (
          <ReasoningTrace result={result} />
        )}
      </div>
    </div>
  );
}
