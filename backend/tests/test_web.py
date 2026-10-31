import json
import threading
import urllib.error
import urllib.request
from datetime import datetime

import pytest

from app import config, serve


# ---- schedule logic
def dt(s):
    return datetime.strptime(s, "%Y-%m-%d %H:%M")

T = ["07:30", "17:30"]

def test_slot_due_basic():
    assert serve.slot_due(dt("2026-10-28 07:31"), T, None) == "2026-10-28 07:30"       # Wednesday
    assert serve.slot_due(dt("2026-10-28 07:31"), T, "2026-10-28 07:30") is None       # already ran
    assert serve.slot_due(dt("2026-10-28 06:00"), T, None) is None                     # before first slot
    assert serve.slot_due(dt("2026-10-28 18:00"), T, "2026-10-28 07:30") == "2026-10-28 17:30"

def test_slot_due_weekend_and_grace():
    assert serve.slot_due(dt("2026-10-31 08:00"), T, None) is None                     # Saturday
    assert serve.slot_due(dt("2026-10-28 14:00"), T, None) is None                     # 07:30 missed by 6.5h

def test_next_run_skips_weekend():
    assert serve.next_run(dt("2026-10-30 18:00"), T) == dt("2026-11-02 07:30")         # Fri evening -> Mon
    assert serve.next_run(dt("2026-10-28 08:00"), T) == dt("2026-10-28 17:30")
    assert serve.next_run(dt("2026-10-28 08:00"), []) is None


# ---- server against a freshly seeded demo database
@pytest.fixture(scope="module")
def site(tmp_path_factory):
    from app import demo
    old = config.DB_PATH
    path = str(tmp_path_factory.mktemp("demo") / "demo.db")
    demo.seed(path)
    srv = serve.make_server("127.0.0.1", 0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    config.DB_PATH = old


def call(host, path, method="GET", headers=None, hosthdr=None):
    h = dict(headers or {})
    if hosthdr:
        h["Host"] = hosthdr
    req = urllib.request.Request(f"http://{host}{path}", method=method, headers=h, data=b"" if method == "POST" else None)
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def test_index_and_health(site):
    code, body = call(site, "/")
    assert code == 200 and b"StockBot" in body and b"id=\"tabs\"" in body
    code, body = call(site, "/api/health")
    h = json.loads(body)
    assert code == 200 and h["demo"] is True and h["tickers"] == 22

def test_data_endpoints(site):
    for p in ["/api/tickers", "/api/recommendations", "/api/recommendations/history", "/api/portfolio",
              "/api/performance", "/api/execution/status", "/api/tickets", "/api/fundamentals/DEMO1", "/api/news?symbol=DEMO1"]:
        code, body = call(site, p)
        assert code == 200, p
        json.loads(body)
    code, body = call(site, "/api/prices/DEMO1?days=50")
    assert code == 200 and len(json.loads(body)) == 50
    assert call(site, "/api/prices/NOPE")[0] == 404
    assert call(site, "/api/nothing")[0] == 404

def test_bad_host_refused(site):
    assert call(site, "/api/health", hosthdr="evil.example.com")[0] == 403

def test_post_security(site):
    ok = {"X-Requested-With": "stockbot"}
    assert call(site, "/api/run-now", "POST")[0] == 400                                         # missing header
    assert call(site, "/api/run-now", "POST", {**ok, "Origin": "http://evil.example.com"})[0] == 403
    assert call(site, "/api/run-now", "POST", {**ok, "Origin": "null"})[0] == 403
    # order actions without a configured token are refused (403), never executed
    assert call(site, "/api/execution/halt", "POST", ok)[0] == 403
    assert call(site, "/api/tickets/1/approve", "POST", ok)[0] == 403
    assert call(site, "/api/tickets/1/approve", "POST", {**ok, "X-Approve-Token": "guess"})[0] == 403

def test_wrong_token_when_configured(site, monkeypatch):
    monkeypatch.setattr(config, "APPROVE_TOKEN", "s3cret-token-value")
    ok = {"X-Requested-With": "stockbot"}
    assert call(site, "/api/execution/halt", "POST", {**ok, "X-Approve-Token": "wrong"})[0] == 401
