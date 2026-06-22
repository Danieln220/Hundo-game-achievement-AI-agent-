export interface Source {
  title: string;
  url: string;
  content?: string;
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
  chart_pending?: boolean;
  chart_url?: string | null;
  done?: boolean;
  steam_id?: string;
  question?: string;
}

export interface SessionResult {
  steam_id: string;
  games: number;
}

export interface Turn {
  question: string;
  answer: string;
}
