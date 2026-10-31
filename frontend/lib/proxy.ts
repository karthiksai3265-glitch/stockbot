// Server-side proxy to the StockBot API for order actions. The approval token lives only in this server's environment
// (STOCKBOT_APPROVE_TOKEN) and is never sent to the browser.
import { NextResponse } from "next/server";

const BASE = process.env.BACKEND_URL || "http://localhost:8000";

export async function forward(req: Request, path: string): Promise<NextResponse> {
  // Block cross-site requests: another website must not be able to trigger an approval from your browser.
  const origin = req.headers.get("origin");
  if (origin && new URL(origin).host !== req.headers.get("host")) {
    return NextResponse.json({ error: "Cross-site request refused" }, { status: 403 });
  }
  if (req.headers.get("x-stockbot-confirm") !== "yes") {
    return NextResponse.json({ error: "Missing confirmation header" }, { status: 400 });
  }
  try {
    const res = await fetch(`${BASE}${path}`, {
      method: "POST",
      headers: { "X-Requested-With": "stockbot", "X-Approve-Token": process.env.STOCKBOT_APPROVE_TOKEN || "" },
      cache: "no-store",
      signal: AbortSignal.timeout(20000),
    });
    const body = await res.json().catch(() => ({}));
    return NextResponse.json(res.ok ? body : { error: body.detail || "Request failed" }, { status: res.status });
  } catch {
    return NextResponse.json({ error: "Could not reach the StockBot API" }, { status: 502 });
  }
}
