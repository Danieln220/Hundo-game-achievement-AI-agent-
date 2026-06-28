export interface Source {
  title: string;
  url: string;
  content?: string;
}

export interface Achievement {
  name: string;
  rarity_pct?: number | null;
  description?: string;
  hidden?: boolean;
  game?: string;
  category?: string;   // Roadmap v2 phase tag (LLM-inferred)
  missable?: boolean;  // Roadmap v2 (best-effort)
}

export interface RoadmapPhase {
  key: string;
  title: string;
  warn?: boolean;
  achievements: Achievement[];
}

export interface RoadmapData {
  target: string;
  total: number;
  unlocked: number;
  pct_done: number;
  remaining: number;
  shown?: number;
  tiers: { quick: Achievement[]; moderate: Achievement[]; challenge: Achievement[] };
  tier_counts: { quick: number; moderate: number; challenge: number };
  howto: { name: string; url: string }[];
  phases?: RoadmapPhase[];
}

export interface AuditData {
  intro?: string;
  total_unlocked?: number;
  total_achievements?: number;
  overall_pct?: number;
  games_total?: number;
  games_started?: number;
  games_completed?: number;
  rarest?: Achievement;
  easy_wins?: Achievement[];
  abandoned?: { game: string; pct: number; remaining: number }[];
  momentum?: { last_unlock?: string; unlocks_last_30d?: number };
  focus?: { game: string; remaining: number; pct: number };
}

export interface AskResult {
  answer?: string;
  route?: string;
  plan?: string;
  interpretation?: string | null;
  code_history?: string[];
  last_code?: string;
  last_result?: string | null;
  last_error?: string | null;
  retries?: number;
  insight?: string | null;
  sources?: Source[];
  roadmap?: RoadmapData | null;
  audit?: AuditData | null;
  chart_pending?: boolean;
  chart_url?: string | null;
  done?: boolean;
  steam_id?: string;
  question?: string;
}

export interface SessionResult {
  steam_id: string;
  persona: string;
  avatar: string;
  games: number;
  unlocked: number;
  total: number;
  perfect: number;
}

// /session returns either a ready summary or a "building" handle to poll.
export type SessionResponse =
  | ({ status: "ready" } & SessionResult)
  | { status: "building"; steam_id: string };

// /session/status — polled while a snapshot builds in the background.
export type SessionStatus =
  | ({ status: "ready" } & SessionResult)
  | { status: "building"; progress: { done: number; total: number; pct: number } }
  | { status: "failed"; error: string };

export interface Turn {
  question: string;
  answer: string;
}

// ── Trophy Case (library view, Step 17) ──────────────────────────────────────
export interface Card {
  name: string;
  desc: string;
  game: string;
  icon: string;
  pct: number | null;
  achieved: boolean;
  t: number | null;
  hidden: boolean;
}
export interface LibGame {
  game: string;
  app: number;
  total: number;
  unlocked: number;
  pct: number;
  avg: number;
  play: number;
  achievements: Card[];
}
export interface Curator {
  rarest: { name: string; game: string; pct: number | null }[];
  quick: { name: string; game: string; pct: number | null }[];
  closest: { game: string; pct: number; remaining: number } | null;
  stalled: { game: string; pct: number }[];
  beatable: { game: string; total: number; userPct: number; avg: number }[];
}
export interface LibraryProfile {
  name: string;
  avatar: string;
  gamesTotal: number;
  gamesWithAch: number;
  started: number;
  perfect: number;
  unlocked: number;
  total: number;
  overall: number;
}
export interface LibraryData {
  profile: LibraryProfile;
  cards: Card[];
  games: LibGame[];
  curator: Curator;
  mix: { common: number; uncommon: number; rare: number; ultra: number };
  flex: Card[];
  library: string[];
}
