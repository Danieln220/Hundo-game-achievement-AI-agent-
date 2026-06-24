const LABELS: Record<string, string> = {
  inspect_schema: "🎮 Loading your save data",
  plan_code: "🎯 Plotting the strategy",
  execute_code: "⚡ Crunching the stats",
  validate_output: "🛡️ Checking for glitches",
  finalize: "🏆 Tallying the score",
  chitchat: "👾 Saying hi",
  clarify: "🕹️ Need a tip-off",
  howto_search: "🔦 Searching the guides",
  roadmap: "🗺️ Charting your quest",
  audit: "🏅 Scouting your trophy case",
  time_estimate: "⏱️ Estimating the grind",
};

export default function ProgressSteps({ nodes }: { nodes: string[] }) {
  const current = nodes[nodes.length - 1];
  const label = current ? LABELS[current] ?? "Working…" : "🕹️ Booting up…";
  return (
    <div className="progress-steps">
      <span className="spinner" />
      <span>{label}</span>
    </div>
  );
}
