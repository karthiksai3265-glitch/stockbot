"""Offline tests: DB schema, price/fundamental storage, news scoring and dedupe, broker stub."""
import numpy as np
import pandas as pd
import pytest

from app import config, db
from app.broker import OrderRequest, get_broker
from app.data import fundamentals, news, prices


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "")  # force keyword fallback
    db.init_db()


def _bars(n=30):
    idx = pd.bdate_range("2026-01-01", periods=n)
    c = 100 + np.cumsum(np.random.default_rng(1).normal(0, 1, n))
    return pd.DataFrame({"Open": c, "High": c + 1, "Low": c - 1, "Close": c, "Adj Close": c,
                         "Volume": np.full(n, 1e6)}, index=idx)


def test_prices_roundtrip_and_upsert():
    with db.connect() as conn:
        assert prices.store_bars(conn, "AAA", _bars()) == 30
        assert prices.store_bars(conn, "AAA", _bars()) == 30  # same rows again -> upsert, no dupes
    df = prices.get_prices("AAA")
    assert len(df) == 30 and df.index.is_monotonic_increasing


def test_fundamentals_extraction():
    cols = pd.to_datetime(["2026-03-31", "2025-12-31"])
    income = pd.DataFrame({cols[0]: [200.0, 80.0, 40.0, 30.0], cols[1]: [180.0, 70.0, 35.0, 25.0]},
                          index=["Total Revenue", "Gross Profit", "Operating Income", "Net Income"])
    balance = pd.DataFrame({cols[0]: [50.0, 20.0]}, index=["Total Debt", "Cash And Cash Equivalents"])
    rows = fundamentals.extract_rows("AAA", income, balance, pd.DataFrame())
    assert len(rows) == 2
    latest = [r for r in rows if r[1] == "2026-03-31"][0]
    assert latest[2] == 200.0 and latest[5] == 30.0 and latest[6] == 50.0  # revenue, net income, debt
    with db.connect() as conn:
        conn.executemany("INSERT INTO fundamentals(symbol,period_end,revenue,gross_profit,operating_income,net_income,"
                         "total_debt,cash,operating_cash_flow,free_cash_flow,shares_outstanding) "
                         "VALUES(?,?,?,?,?,?,?,?,?,?,?)", rows)
    assert len(fundamentals.get_fundamentals("AAA")) == 2


def test_keyword_sentiment():
    assert news.keyword_sentiment("Company beats estimates, record profit") > 0
    assert news.keyword_sentiment("Shares plunge after lawsuit and downgrade") < 0
    assert news.keyword_sentiment("Company holds annual meeting") == 0


def test_news_store_dedupes_and_scores():
    items = [{"title": "AAA surges on record growth", "url": "http://x/1", "publisher": "P", "published": "2026-10-01"},
             {"title": "AAA faces probe", "url": "http://x/2", "publisher": "P", "published": "2026-10-02"}]
    with db.connect() as conn:
        assert news.store_news(conn, "AAA", items) == 2
        assert news.store_news(conn, "AAA", items) == 0  # duplicates skipped
    s = news.recent_sentiment("AAA")
    assert -1 <= s <= 1


def test_manual_broker_never_executes():
    b = get_broker()
    assert b.name == "manual" and not b.supports_auto_execution
    r = b.place_order(OrderRequest("AAA", "buy", 10, limit_price=101.5, stop_loss=95, take_profit=115))
    assert r.accepted is False and "AAA" in r.message and "BUY" in r.message
