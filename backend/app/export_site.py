"""Build a single static web page (site/index.html) from the current database, for free hosting (GitHub Pages).

    python -m app.export_site [--out site/index.html]

The page contains a snapshot of everything the dashboard shows. A scheduled job runs `app.run_daily` and then this,
so the hosted site refreshes itself. Read-only: the Update and order buttons are disabled on the hosted page.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from urllib.parse import quote

from . import queries, serve

TEMPLATE = Path(__file__).parent / "web" / "index.html"
SHIM = """const SNAP = %s;
window.fetch = async (u, o) => {
  const j = (c, b) => new Response(JSON.stringify(b), { status: c, headers: { "Content-Type": "application/json" } });
  if (o && o.method && o.method !== "GET") return j(403, { detail: "This hosted page is read-only. Run the program on your computer to place orders." });
  const k = String(u);
  return k in SNAP ? j(200, SNAP[k]) : j(404, { detail: "No data for this request" });
};
"""


def snapshot() -> dict:
    snap: dict = {}

    def add(url, path, q=None):
        snap[url] = serve.get_route(path, q or {})

    for p in ["/api/health", "/api/tickers", "/api/recommendations", "/api/recommendations/history", "/api/portfolio",
              "/api/performance", "/api/execution/status", "/api/tickets"]:
        add(p, p)
    for t in queries.tickers():
        s, q = t["symbol"], quote(t["symbol"])
        add(f"/api/prices/{q}?days=250", f"/api/prices/{s}", {"days": ["250"]})
        add(f"/api/fundamentals/{q}", f"/api/fundamentals/{s}")
        add(f"/api/news?symbol={q}", "/api/news", {"symbol": [s]})
    snap["/api/health"]["update"] = {"scheduled": True, "running": False, "next_run": None, "last_status": None,
                                     "times": [], "timezone": "", "last_finished": None}
    return snap


def build(out: str) -> int:
    html = TEMPLATE.read_text()
    data = json.dumps(snapshot(), default=str).replace("</", "<\\/")
    script = re.search(r"<script>(.*)</script>", html, re.S).group(1)
    html = html.replace(script, SHIM % data + script)
    html = html.replace('$("#updBtn").hidden = !h || h.demo;', '$("#updBtn").hidden = true;')   # read-only hosted page
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(html)
    return len(html)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="site/index.html")
    print("wrote", build(ap.parse_args().out), "bytes")
