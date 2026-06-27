// Contextual follow-up chips — derived from the LAST assistant result so the
// suggestions stay relevant to where the conversation is (instead of the static
// starter set, which only shows on the empty welcome screen). Purely client-side
// and deterministic: keys off result.route (howto | clarify | chitchat | roadmap
// | audit | timecost | analysis) and, where possible, the actual game in the
// result (audit focus game, roadmap target) so a chip can say the real name.

import type { AskResult } from "./types";

export interface Followup {
  label: string;
  q: string;
}

const NEXT: Followup = { label: "🎯 What should I play next?", q: "What should I play next?" };
const AUDIT: Followup = { label: "🏅 Full profile audit", q: "Give me a full audit of my profile" };
const EASY: Followup = { label: "🟢 Cross-game easy wins", q: "Show me cross-game easy wins" };
const RAREST: Followup = { label: "💎 My rarest achievements", q: "What are my top 3 rarest achievements?" };

export function followupsFor(r: AskResult | undefined): Followup[] {
  if (!r) return [];

  switch (r.route) {
    case "audit": {
      const chips: Followup[] = [];
      const focus = r.audit?.focus?.game;
      if (focus)
        chips.push({ label: `🗺️ Roadmap for ${focus}`, q: `Build me a roadmap to 100% ${focus}` });
      chips.push(NEXT, RAREST);
      return chips;
    }

    case "roadmap": {
      const chips: Followup[] = [];
      const target = r.roadmap?.target;
      if (target)
        chips.push({ label: `⏱️ Time to 100% ${target}`, q: `How long would it take me to 100% ${target}?` });
      chips.push(NEXT, EASY);
      return chips;
    }

    case "timecost":
      return [NEXT, AUDIT, EASY];

    case "howto":
      return [NEXT, EASY, AUDIT];

    case "clarify":
      // The agent is asking the user a question — don't distract with chips.
      return [];

    case "analysis":
    case "chitchat":
    default:
      return [AUDIT, EASY, NEXT];
  }
}
