"""Build a single static web page (site/index.html) from the current database, for free hosting (GitHub Pages).

    python -m app.export_site [--out site/index.html]

The page contains a snapshot of everything the dashboard shows. A scheduled job runs `app.run_daily` and then this,
so the hosted site refreshes itself. Read-only: the Update and order buttons are disabled on the hosted page.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
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


LOCK_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex"><title>StockBot</title>
<style>:root{color-scheme:light dark;--bg:#f1f4f7;--ink:#101c27;--line:#c9d3db;--accent:#0a6c8a}
@media(prefers-color-scheme:dark){:root{--bg:#0b1218;--ink:#e4ebf0;--line:#2a3b49;--accent:#4db9d8}}
body{margin:0;min-height:100vh;display:grid;place-items:center;background:var(--bg);color:var(--ink);font:15px system-ui,sans-serif}
form{display:grid;gap:12px;width:min(320px,86vw)}h1{font-size:20px;margin:0}
input,button{font:inherit;padding:10px 12px;border-radius:8px;border:1px solid var(--line);background:transparent;color:inherit}
button{background:var(--accent);border-color:var(--accent);color:#fff;cursor:pointer}p{margin:0;min-height:1.3em;color:#b93434}</style></head>
<body><form id="f"><h1>StockBot</h1><input id="pw" type="password" placeholder="Password" autocomplete="current-password" autofocus>
<button>Open</button><p id="e" role="alert"></p></form>
<script>
const BLOB="__BLOB__";
const b64=s=>Uint8Array.from(atob(s),c=>c.charCodeAt(0));
document.getElementById("f").addEventListener("submit",async ev=>{
  ev.preventDefault(); const e=document.getElementById("e"); e.textContent="";
  try{
    const raw=b64(BLOB),salt=raw.slice(0,16),iv=raw.slice(16,28),ct=raw.slice(28);
    const km=await crypto.subtle.importKey("raw",new TextEncoder().encode(document.getElementById("pw").value),"PBKDF2",false,["deriveKey"]);
    const key=await crypto.subtle.deriveKey({name:"PBKDF2",salt,iterations:600000,hash:"SHA-256"},km,{name:"AES-GCM",length:256},false,["decrypt"]);
    const html=new TextDecoder().decode(await crypto.subtle.decrypt({name:"AES-GCM",iv},key,ct));
    document.open();document.write(html);document.close();
  }catch(x){e.textContent=(window.crypto&&crypto.subtle)?"Wrong password.":"Needs a secure (https) connection.";}
});
</script></body></html>"""


def encrypt(html: str, password: str) -> str:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    salt, iv = os.urandom(16), os.urandom(12)
    key = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=600_000).derive(password.encode())
    blob = base64.b64encode(salt + iv + AESGCM(key).encrypt(iv, html.encode(), None)).decode()
    return LOCK_PAGE.replace("__BLOB__", blob)


def build(out: str, password: str | None = None) -> int:
    html = TEMPLATE.read_text()
    data = json.dumps(snapshot(), default=str).replace("</", "<\\/")
    script = re.search(r"<script>(.*)</script>", html, re.S).group(1)
    html = html.replace(script, SHIM % data + script)
    html = html.replace('$("#updBtn").hidden = !h || h.demo;', '$("#updBtn").hidden = true;')   # read-only hosted page
    if password:
        html = encrypt(html, password)
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(html)
    return len(html)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="site/index.html")
    ap.add_argument("--password-env", default="SITE_PASSWORD", help="env var holding an optional password; if set, the page is encrypted")
    a = ap.parse_args()
    pw = os.getenv(a.password_env) or None
    print("wrote", build(a.out, pw), "bytes", "(password-locked)" if pw else "(open, no password)")
