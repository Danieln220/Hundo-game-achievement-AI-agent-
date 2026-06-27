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
