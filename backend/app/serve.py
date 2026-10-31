"""The website. One command, no Node, no extra packages:

    python -m app.serve --demo        # fictional demo data, to see everything working in seconds
    python -m app.serve --schedule    # real data; also runs the full update by itself at STOCKBOT_RUN_TIMES (weekdays)
    python -m app.serve               # real data, no automatic updates (run `python -m app.run_daily` yourself)

Then open http://localhost:8000. The server only listens on this computer unless you pass --host 0.0.0.0.

Security: order approval and the kill switch need STOCKBOT_APPROVE_TOKEN (typed into the page, never stored in it);
every POST must come from this site (Origin check) with a custom header, and the Host header must be local, which
blocks other websites from driving your server through your browser.
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import threading
import webbrowser
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse
from zoneinfo import ZoneInfo

from . import config, db, execution, portfolio, queries, recs_store

log = logging.getLogger("serve")
INDEX = Path(__file__).parent / "web" / "index.html"
LOCAL_HOSTS = {"localhost", "127.0.0.1", "[::1]"}


# --------------------------------------------------------------------------- schedule logic (pure, tested)
def parse_times(times: list[str]) -> list[tuple[int, int]]:
    out = []
    for t in times:
        h, m = t.split(":")
        out.append((int(h), int(m)))
    return sorted(out)


def slot_due(now: datetime, times: list[str], last_slot: str | None, grace_hours: float = 6) -> str | None:
    """The slot key ("YYYY-MM-DD HH:MM") that should run now, or None. Weekdays only; a slot missed by more than
    `grace_hours` is skipped rather than run late."""
    if now.weekday() >= 5:
        return None
    past = [now.replace(hour=h, minute=m, second=0, microsecond=0) for h, m in parse_times(times)]
    past = [s for s in past if s <= now]
    if not past:
        return None
    slot = max(past)
    if now - slot > timedelta(hours=grace_hours):
        return None
    key = slot.strftime("%Y-%m-%d %H:%M")
    return None if key == last_slot else key


def next_run(now: datetime, times: list[str]) -> datetime | None:
    if not times:
        return None
    for add in range(0, 8):
        day = now + timedelta(days=add)
        if day.weekday() >= 5:
            continue
        for h, m in parse_times(times):
            s = day.replace(hour=h, minute=m, second=0, microsecond=0)
            if s > now:
                return s
    return None


class Runner:
    """Runs the full update in a background thread, one at a time."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.running = False
        self.scheduled = False

    def start(self, reason: str) -> bool:
        if not self._lock.acquire(blocking=False):
            return False
        self.running = True
        threading.Thread(target=self._go, args=(reason,), daemon=True).start()
        return True

    def _go(self, reason: str) -> None:
        from . import run_daily                     # imported here: pulls in the data modules only when an update runs
        log.info("Update started (%s)", reason)
        db.set_meta("last_run_started", datetime.now(timezone.utc).isoformat())
        try:
            run_daily.run_once()
            db.set_meta("last_run_status", "ok")
        except Exception as e:
            log.exception("Update failed")
            db.set_meta("last_run_status", f"failed: {e}")
        finally:
            db.set_meta("last_run_finished", datetime.now(timezone.utc).isoformat())
            self.running = False
            self._lock.release()
            log.info("Update finished")

    def status(self) -> dict:
        tz = ZoneInfo(config.TIMEZONE)
        nxt = next_run(datetime.now(tz), config.RUN_TIMES) if self.scheduled else None
        return {"scheduled": self.scheduled, "times": config.RUN_TIMES, "timezone": config.TIMEZONE, "running": self.running,
                "next_run": nxt.isoformat() if nxt else None, "last_finished": db.get_meta("last_run_finished"),
                "last_status": db.get_meta("last_run_status")}


RUNNER = Runner()


def scheduler_loop(stop: threading.Event) -> None:
    tz = ZoneInfo(config.TIMEZONE)
    if db.get_meta("last_run_finished") is None:
        RUNNER.start("first run")                  # fresh install: fetch data straight away
    while not stop.wait(30):
        key = slot_due(datetime.now(tz), config.RUN_TIMES, db.get_meta("last_slot"))
        if key and RUNNER.start(f"scheduled {key}"):
            db.set_meta("last_slot", key)


# --------------------------------------------------------------------------- HTTP
class HttpError(Exception):
    def __init__(self, code: int, detail: str):
        super().__init__(detail)
        self.code, self.detail = code, detail


def health() -> dict:
    out = queries.data_status()
    out["update"] = RUNNER.status()
    out["broker_mode"] = execution.status()["mode"]
    return out


def get_route(path: str, q: dict) -> object:
    simple = {"/api/health": health, "/api/tickers": queries.tickers, "/api/recommendations": recs_store.latest_batch,
              "/api/recommendations/history": recs_store.history, "/api/portfolio": portfolio.snapshot,
              "/api/performance": recs_store.performance, "/api/execution/status": execution.status, "/api/tickets": execution.list_tickets}
    if path in simple:
        return simple[path]()
    if path == "/api/news":
        return queries.news((q.get("symbol") or [None])[0], int((q.get("limit") or ["30"])[0]))
    m = re.fullmatch(r"/api/prices/([^/]+)", path)
    if m:
        rows = queries.prices(unquote(m.group(1)), int((q.get("days") or ["250"])[0]))
        if not rows:
            raise HttpError(404, "No price data for this symbol yet")
        return rows
    m = re.fullmatch(r"/api/fundamentals/([^/]+)", path)
    if m:
        return queries.fundamentals(unquote(m.group(1)))
    raise HttpError(404, "Not found")


def post_route(path: str, token: str | None) -> object:
    if path == "/api/run-now":
        if not RUNNER.start("manual"):
            raise HttpError(409, "An update is already running")
        return {"ok": True, "started": True}

    order_action = re.fullmatch(r"/api/tickets/(\d+)/(approve|reject|cancel)", path)
    other = re.fullmatch(r"/api/(tickets/propose|execution/halt|execution/resume)", path)
    if not (order_action or other):
        raise HttpError(404, "Not found")
    problem = execution.check_token(token, config.APPROVE_TOKEN)
    if problem:
        raise HttpError(403 if not config.APPROVE_TOKEN else 401, problem)

    if order_action:
        res = {"approve": execution.approve, "reject": execution.reject, "cancel": execution.cancel}[order_action.group(2)](int(order_action.group(1)))
        if not res.get("ok", True):
            raise HttpError(400, res.get("error") or "Not allowed")
        return res
    which = other.group(1)
    if which == "tickets/propose":
        return execution.propose()
    execution.set_halted(which.endswith("halt"))
    return {"ok": True, "halted": which.endswith("halt")}


def host_name(header: str | None) -> str:
    h = (header or "").strip().lower()
    return h.split("]")[0] + "]" if h.startswith("[") else h.split(":")[0]


class Handler(BaseHTTPRequestHandler):
    server_version = "StockBot"
    protocol_version = "HTTP/1.1"
    allow_any_host = False

    def log_message(self, fmt, *args):      # keep the console quiet
        log.debug("%s %s", self.address_string(), fmt % args)

    # -- plumbing
    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, obj: object) -> None:
        self._send(code, json.dumps(obj, default=str).encode(), "application/json; charset=utf-8")

    def _check_host(self) -> bool:
        if not self.allow_any_host and host_name(self.headers.get("Host")) not in LOCAL_HOSTS:
            self._json(403, {"detail": "Unexpected Host header"})
            return False
        return True

    # -- verbs
    def do_GET(self):
        if not self._check_host():
            return
        u = urlparse(self.path)
        try:
            if u.path in ("/", "/index.html"):
                return self._send(200, INDEX.read_bytes(), "text/html; charset=utf-8")
            if u.path.startswith("/api/"):
                return self._json(200, get_route(u.path, parse_qs(u.query)))
            raise HttpError(404, "Not found")
        except HttpError as e:
            self._json(e.code, {"detail": e.detail})
        except Exception:
            log.exception("GET %s failed", self.path)
            self._json(500, {"detail": "Server error"})

    def do_POST(self):
        if not self._check_host():
            return
        try:
            origin = self.headers.get("Origin")
            if origin and urlparse(origin).netloc.lower() != (self.headers.get("Host") or "").lower():
                raise HttpError(403, "Cross-site request refused")      # also refuses "null" origins
            if self.headers.get("X-Requested-With") != "stockbot":
                raise HttpError(400, "Missing X-Requested-With header")
            length = int(self.headers.get("Content-Length") or 0)
            if length:
                self.rfile.read(min(length, 65536))      # bodies are not used; drain so keep-alive stays in sync
            self._json(200, post_route(urlparse(self.path).path, self.headers.get("X-Approve-Token")))
        except HttpError as e:
            self._json(e.code, {"detail": e.detail})
        except Exception:
            log.exception("POST %s failed", self.path)
            self._json(500, {"detail": "Server error"})

    def do_OPTIONS(self):                       # no CORS: cross-origin pages cannot use this API
        self._send(405, b"", "text/plain")


def make_server(host: str = "127.0.0.1", port: int = 8000) -> ThreadingHTTPServer:
    Handler.allow_any_host = host not in ("127.0.0.1", "localhost", "::1")
    srv = ThreadingHTTPServer((host, port), Handler)
    srv.daemon_threads = True
    return srv


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser(description="StockBot website")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--db", help="database file (default: backend/stockbot.db)")
    ap.add_argument("--demo", action="store_true", help="use fictional demo data (backend/demo.db, built if missing)")
    ap.add_argument("--schedule", action="store_true", help="run the full update automatically at STOCKBOT_RUN_TIMES on weekdays")
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args()

    if a.demo:
        from . import demo
        print("Preparing demo data (about 15 seconds the first time)...")
        demo.ensure(a.db)
        a.schedule = False
    elif a.db:
        config.DB_PATH = a.db
    db.init_db()
    if a.host not in ("127.0.0.1", "localhost", "::1"):
        print("WARNING: listening on a non-local address. Anyone on your network can open this site. Order actions still need your token.")

    stop = threading.Event()
    if a.schedule:
        RUNNER.scheduled = True
        threading.Thread(target=scheduler_loop, args=(stop,), daemon=True).start()
        print(f"Automatic updates on weekdays at {', '.join(config.RUN_TIMES)} ({config.TIMEZONE}).")
    try:
        srv = make_server(a.host, a.port)
    except OSError as e:
        raise SystemExit(f"Could not start on port {a.port}: {e}. Try --port {a.port + 1}.")
    url = f"http://localhost:{a.port}"
    print(f"StockBot is running at {url}   (Ctrl+C to stop)")
    if not a.no_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        stop.set()


if __name__ == "__main__":
    main()
