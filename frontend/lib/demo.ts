// Deterministic SAMPLE data so the site is usable before steps 3-5 exist. Never real predictions.
import type { Bar, Fundamental, ModelVersion, NewsItem, Performance, Portfolio, Rec } from "./types";

function rng(seed: number) {
  let s = seed >>> 0;
  return () => ((s = (s * 1664525 + 1013904223) >>> 0) / 4294967296);
}

export function demoBars(symbol: string, n = 250): Bar[] {
  const r = rng([...symbol].reduce((a, c) => a * 31 + c.charCodeAt(0), 7));
  let p = 60 + r() * 140;
  const out: Bar[] = [];
  const d = new Date("2026-10-28T00:00:00Z");
  d.setUTCDate(d.getUTCDate() - Math.round(n * 1.45));
  while (out.length < n) {
    d.setUTCDate(d.getUTCDate() + 1);
    if (d.getUTCDay() === 0 || d.getUTCDay() === 6) continue;
    const open = p;
    p = Math.max(5, p * (1 + (r() - 0.48) * 0.03));
    out.push({ date: d.toISOString().slice(0, 10), open, high: Math.max(open, p) * 1.005, low: Math.min(open, p) * 0.995, close: p, volume: Math.round(1e6 + r() * 4e6) });
  }
  return out;
}

const NAMES: Record<string, [string, "SP500" | "ASX200"]> = {
  NVDA: ["NVIDIA", "SP500"], MSFT: ["Microsoft", "SP500"], JPM: ["JPMorgan Chase", "SP500"], XOM: ["Exxon Mobil", "SP500"],
  "BHP.AX": ["BHP Group", "ASX200"], "CSL.AX": ["CSL", "ASX200"], "CBA.AX": ["Commonwealth Bank", "ASX200"],
};

export const DEMO_RECS: Rec[] = [
  { id: 1, created: "2026-10-28T08:30:00+11:00", symbol: "NVDA", action: "BUY", entry: 142.3, window: "At open, within 30 min", target: 155.0, stop: 136.5, sizePct: 4, confidence: 0.71,
    reasons: ["Revenue growth accelerating for 3 straight quarters", "Price above 50 and 200-day averages", "Recent headlines mostly positive"], scores: { technical: 0.6, fundamental: 0.7, sentiment: 0.4 } },
  { id: 2, created: "2026-10-28T08:30:00+11:00", symbol: "CSL.AX", action: "BUY", entry: 272.4, window: "First hour of ASX trading", target: 292.0, stop: 262.0, sizePct: 3, confidence: 0.62,
    reasons: ["Margins improving", "Pullback to support level", "Low debt relative to cash flow"], scores: { technical: 0.3, fundamental: 0.6, sentiment: 0.1 } },
  { id: 3, created: "2026-10-28T08:30:00+11:00", symbol: "XOM", action: "SELL", entry: 108.9, window: "At open", target: 101.5, stop: 112.8, sizePct: 2, confidence: 0.55,
    reasons: ["Downtrend with weakening momentum", "Earnings estimates revised lower", "Negative sentiment on oil demand news"], scores: { technical: -0.5, fundamental: -0.2, sentiment: -0.4 } },
  { id: 4, created: "2026-10-28T08:30:00+11:00", symbol: "BHP.AX", action: "BUY", entry: 44.1, window: "First hour of ASX trading", target: 47.5, stop: 42.4, sizePct: 3, confidence: 0.58,
    reasons: ["Breakout above resistance on rising volume", "Strong free cash flow", "Commodity news neutral to positive"], scores: { technical: 0.5, fundamental: 0.4, sentiment: 0.1 } },
].map((r) => ({ ...r, name: NAMES[r.symbol][0], market: NAMES[r.symbol][1] })) as Rec;

export const DEMO_HISTORY: Rec[] = [
  { id: 101, created: "2026-10-27T08:30:00+11:00", symbol: "MSFT", action: "BUY", entry: 431.0, window: "At open", target: 455, stop: 420, sizePct: 4, confidence: 0.66, reasons: ["Sample"], scores: { technical: 0.4, fundamental: 0.6, sentiment: 0.2 }, outcome: { status: "WIN", pnlPct: 3.1 } },
  { id: 102, created: "2026-10-24T08:30:00+11:00", symbol: "JPM", action: "BUY", entry: 245.2, window: "At open", target: 258, stop: 238, sizePct: 3, confidence: 0.6, reasons: ["Sample"], scores: { technical: 0.2, fundamental: 0.5, sentiment: 0.1 }, outcome: { status: "LOSS", pnlPct: -2.9 } },
  { id: 103, created: "2026-10-23T08:30:00+11:00", symbol: "CBA.AX", action: "SELL", entry: 178.0, window: "First hour", target: 168, stop: 183, sizePct: 2, confidence: 0.52, reasons: ["Sample"], scores: { technical: -0.4, fundamental: -0.1, sentiment: -0.2 }, outcome: { status: "WIN", pnlPct: 2.2 } },
  { id: 104, created: "2026-10-22T08:30:00+11:00", symbol: "NVDA", action: "BUY", entry: 138.0, window: "At open", target: 150, stop: 132, sizePct: 4, confidence: 0.7, reasons: ["Sample"], scores: { technical: 0.5, fundamental: 0.7, sentiment: 0.3 }, outcome: { status: "OPEN", pnlPct: 3.1 } },
].map((r) => ({ ...r, name: NAMES[r.symbol][0], market: NAMES[r.symbol][1] })) as Rec[];

export function demoFundamentals(): Fundamental[] {
  return [
    ["2025-12-31", 52e9, 11e9, 18e9, 9e9], ["2026-03-31", 55e9, 12.4e9, 18.5e9, 10e9],
    ["2026-06-30", 59e9, 13.9e9, 18.2e9, 11.6e9], ["2026-09-30", 64e9, 15.8e9, 17.9e9, 13e9],
  ].map(([period_end, revenue, net_income, total_debt, free_cash_flow]) => ({ period_end, revenue, net_income, total_debt, free_cash_flow } as Fundamental));
}

export function demoNews(symbol: string): NewsItem[] {
  return [
    { title: `${symbol} beats quarterly revenue estimates, raises outlook`, publisher: "Sample Wire", url: null, sentiment: 0.7, published: "2026-10-27" },
    { title: `Analysts split on ${symbol} valuation after recent rally`, publisher: "Sample Wire", url: null, sentiment: 0.0, published: "2026-10-26" },
    { title: `Regulators open probe into sector pricing practices`, publisher: "Sample Wire", url: null, sentiment: -0.5, published: "2026-10-25" },
  ];
}

export const DEMO_PORTFOLIO: Portfolio = {
  cash: 62400,
  positions: [
    { symbol: "NVDA", qty: 60, avgPrice: 138.0, last: 142.3 }, { symbol: "MSFT", qty: 12, avgPrice: 431.0, last: 438.4 },
    { symbol: "CBA.AX", qty: -30, avgPrice: 178.0, last: 174.1 },
  ],
  equity: Array.from({ length: 60 }, (_, i) => ({ date: `D${i + 1}`, value: 100000 + i * 90 + Math.sin(i / 4) * 900 })),
};

export const DEMO_VERSIONS: ModelVersion[] = [
  { version: "v1 (baseline)", promoted: "2026-09-01", winRate: 0.52, backtestReturnPct: 4.1, note: "Equal-weight technical + fundamental + sentiment" },
  { version: "v2", promoted: "2026-10-01", winRate: 0.56, backtestReturnPct: 6.3, note: "Recalibrated weights; beat v1 in backtest and paper trading" },
];
export const DEMO_PERFORMANCE: Performance = {
  versions: DEMO_VERSIONS,
  accuracy: [{ month: "Aug", winRate: 0.5 }, { month: "Sep", winRate: 0.53 }, { month: "Oct", winRate: 0.57 }],
  log: [
    { date: "2026-10-22", kind: "blend", change: "Blend weights 40/40/20", decision: "skipped", reason: "Only 31 graded live calls so far; 60 are needed" },
    { date: "2026-10-15", kind: "technical", change: "Entry threshold 0.5 to 0.4", decision: "rejected", reason: "Out-of-sample average trade fell from +0.11% to +0.03%" },
    { date: "2026-10-08", kind: "technical", change: "Profit target distance 3.0 to 3.5 x ATR", decision: "rejected", reason: "z 0.4 is below 1.28 (could be luck)" },
    { date: "2026-10-01", kind: "technical", change: "Technical weights: momentum up 0.1, trend down 0.1", decision: "promoted", reason: "Out-of-sample average trade +0.11% vs +0.04% for v1 (z 1.4), 412 trades" },
  ],
};
