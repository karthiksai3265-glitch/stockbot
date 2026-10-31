export type Rec = {
  id: number;
  created: string; // ISO
  symbol: string;
  name: string;
  market: "SP500" | "ASX200" | "NIFTY50";
  action: "BUY" | "SELL";
  entry: number;
  window: string; // e.g. "At open, within 30 min"
  target: number;
  stop: number;
  sizePct: number; // % of portfolio
  confidence: number; // 0..1
  reasons: string[];
  scores: { technical: number; fundamental: number; sentiment: number }; // -1..1
  outcome?: { status: "WIN" | "LOSS" | "OPEN"; pnlPct: number };
};

export type Bar = { date: string; open: number; high: number; low: number; close: number; volume: number };
export type Fundamental = { period_end: string; revenue: number | null; net_income: number | null; total_debt: number | null; free_cash_flow: number | null };
export type NewsItem = { title: string; publisher: string | null; url: string | null; sentiment: number | null; published: string | null };
export type Position = { symbol: string; qty: number; avgPrice: number; last: number };
export type Portfolio = { cash: number; positions: Position[]; equity: { date: string; value: number }[] };
export type ModelVersion = { version: string; promoted: string; winRate: number; backtestReturnPct: number; note: string };
export type LearnLog = { date: string; kind: string; change: string; decision: string; reason: string };
export type Performance = { versions: ModelVersion[]; accuracy: { month: string; winRate: number }[]; log?: LearnLog[] };
export type Source = "live" | "demo";
export type Ticket = {
  id: number; symbol: string; side: "buy" | "sell"; qty: number; limitPrice: number | null; stop: number | null; target: number | null;
  notional: number; status: string; mode: string | null; message: string; created: string; decided: string | null;
};
export type ExecStatus = {
  broker: string; mode: "manual" | "paper" | "live"; halted: boolean; connected: boolean; message: string; web_approval_enabled: boolean;
  limits: { max_order_usd: number; max_daily_orders: number; max_daily_usd: number; ticket_ttl_hours: number };
  today: { orders: number; usd: number }; pending: number; equity?: number; cash?: number;
};
