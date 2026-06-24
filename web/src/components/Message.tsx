import ReactMarkdown from "react-markdown";
import type { Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import { assetUrl } from "../api";
import type { AskResult } from "../types";
import ReasoningTrace from "./ReasoningTrace";
import RoadmapCard from "./RoadmapCard";
import AuditDashboard from "./AuditDashboard";

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

export default function Message(props: Props) {
  if (props.role === "user") {
    return (
      <div className="msg user">
        <div className="bubble">{props.text}</div>
      </div>
    );
  }

  const { result, chartUrl, chartLoading, onAction, retryQuestion, onRetry } = props;
  const hasTrace =
    !!result.plan || (result.code_history?.length ?? 0) > 0;

  // Route-specific rich rendering; markdown is the fallback for everything else.
  const body = result.roadmap ? (
    <RoadmapCard data={result.roadmap} onAction={onAction} />
  ) : result.audit ? (
    <AuditDashboard data={result.audit} onAction={onAction} />
  ) : (
    <div className="markdown">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={mdComponents}>
        {result.answer || "(no answer)"}
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

        {chartLoading && (
          <div className="chart-skeleton" aria-label="Generating chart">
            <span className="skeleton-label">📊 Generating chart…</span>
          </div>
        )}
        {chartUrl && (
          <img className="chart" src={assetUrl(chartUrl)} alt="chart" />
        )}

        {hasTrace && <ReasoningTrace result={result} />}
      </div>
    </div>
  );
}
