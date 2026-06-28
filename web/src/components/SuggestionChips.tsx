// Categorized starter prompts — teaches the app's range (Analyze / Plan / How-to)
// instead of a flat example list. Clicking a chip sends that question.

const CATEGORIES: { label: string; icon: string; chips: string[] }[] = [
  {
    label: "Analyze",
    icon: "🔍",
    chips: [
      "Which games am I closest to finishing?",
      "What are my top 3 rarest achievements?",
      "Give me a full audit of my profile",
    ],
  },
  {
    label: "Plan",
    icon: "🗺️",
    chips: [
      "Build me a roadmap to 100%",
      "What should I play next?",
      "Show me cross-game easy wins",
    ],
  },
  {
    label: "How-to",
    icon: "❓",
    chips: [
      "How do I unlock the rarest achievement in my library?",
      "Which achievements am I stuck on?",
    ],
  },
];

export default function SuggestionChips({
  onPick,
}: {
  onPick: (question: string) => void;
}) {
  return (
    <div className="suggestions">
      {CATEGORIES.map((cat) => (
        <div className="sug-group" key={cat.label}>
          <div className="sug-head">
            <span className="sug-icon">{cat.icon}</span>
            {cat.label}
          </div>
          <div className="sug-chips">
            {cat.chips.map((c) => (
              <button key={c} className="example" onClick={() => onPick(c)}>
                {c}
              </button>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}
