import ReactMarkdown from "react-markdown";
import type { Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import { assetUrl } from "../api";
import type { AskResult } from "../types";
import ReasoningTrace from "./ReasoningTrace";

type Props =
  | { role: "user"; text: string }
  | {
      role: "assistant";
      result: AskResult;
      chartUrl?: string | null;
      chartLoading?: boolean;
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

  const { result, chartUrl, chartLoading } = props;
  const hasTrace =
    !!result.plan || (result.code_history?.length ?? 0) > 0;

  return (
    <div className="msg assistant">
      <div className="bubble">
        <div className="markdown">
          <ReactMarkdown remarkPlugins={[remarkGfm]} components={mdComponents}>
            {result.answer || "(no answer)"}
          </ReactMarkdown>
        </div>

        {result.insight && <div className="insight">💡 {result.insight}</div>}

        {chartLoading && <div className="muted small">Generating chart…</div>}
        {chartUrl && (
          <img className="chart" src={assetUrl(chartUrl)} alt="chart" />
        )}

        {hasTrace && <ReasoningTrace result={result} />}
      </div>
    </div>
  );
}
