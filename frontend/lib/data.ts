// Data access. Tries the Python API (BACKEND_URL); falls back to clearly labeled SAMPLE data.
import { DEMO_HISTORY, DEMO_PERFORMANCE, DEMO_PORTFOLIO, DEMO_RECS, demoBars, demoFundamentals, demoNews } from "./demo";
import type { Bar, ExecStatus, Fundamental, NewsItem, Performance, Portfolio, Rec, Source, Ticket } from "./types";

const BASE = process.env.BACKEND_URL || "http://localhost:8000";
type R<T> = { data: T; source: Source };

async function get<T>(path: string): Promise<T | null> {
  try {
    const res = await fetch(`${BASE}${path}`, { cache: "no-store", signal: AbortSignal.timeout(2500) });
    return res.ok ? ((await res.json()) as T) : null;
  } catch {
    return null;
  }
}

export async function getRecommendations(): Promise<R<Rec[]>> {
  const live = await get<Rec[]>("/api/recommendations");
  return live && live.length ? { data: live, source: "live" } : { data: DEMO_RECS, source: "demo" };
}

export async function getHistory(): Promise<R<Rec[]>> {
  const live = await get<Rec[]>("/api/recommendations/history");
  return live && live.length ? { data: live, source: "live" } : { data: DEMO_HISTORY, source: "demo" };
}

export async function getBars(symbol: string): Promise<R<Bar[]>> {
  const live = await get<Bar[]>(`/api/prices/${encodeURIComponent(symbol)}`);
  return live && live.length ? { data: live, source: "live" } : { data: demoBars(symbol), source: "demo" };
}

export async function getFundamentals(symbol: string): Promise<R<Fundamental[]>> {
  const live = await get<Fundamental[]>(`/api/fundamentals/${encodeURIComponent(symbol)}`);
  return live && live.length ? { data: live, source: "live" } : { data: demoFundamentals(), source: "demo" };
}

export async function getNews(symbol: string): Promise<R<NewsItem[]>> {
  const live = await get<NewsItem[]>(`/api/news?symbol=${encodeURIComponent(symbol)}`);
  return live && live.length ? { data: live, source: "live" } : { data: demoNews(symbol), source: "demo" };
}

export async function getPortfolio(): Promise<R<Portfolio>> {
  // Live once the paper portfolio has been synced at least once (it then has an equity history).
  const live = await get<{ equity: unknown[] }>("/api/portfolio");
  return live && live.equity?.length ? { data: live as unknown as Portfolio, source: "live" } : { data: DEMO_PORTFOLIO, source: "demo" };
}

export async function getPerformance(): Promise<R<Performance>> {
  const live = await get<Performance>("/api/performance");
  return live && live.versions?.length ? { data: live, source: "live" } : { data: DEMO_PERFORMANCE, source: "demo" };
}

export async function getExecStatus(): Promise<ExecStatus | null> {
  return get<ExecStatus>("/api/execution/status");
}

export async function getTickets(): Promise<Ticket[]> {
  return (await get<Ticket[]>("/api/tickets")) ?? [];
}

export const fmt = {
  money: (n: number, cur = "$") => `${n < 0 ? "-" : ""}${cur}${Math.abs(n).toLocaleString("en-US", { maximumFractionDigits: 2 })}`,
  big: (n: number | null) => (n == null ? "–" : Math.abs(n) >= 1e9 ? `${(n / 1e9).toFixed(1)}B` : `${(n / 1e6).toFixed(0)}M`),
  pct: (n: number) => `${n > 0 ? "+" : ""}${n.toFixed(1)}%`,
};
