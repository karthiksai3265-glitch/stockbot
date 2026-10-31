"""Offline tests for analysis, trade rules, backtest, engine, grading and the API data layer."""
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

from app import backtest, config, db, engine, grading, recs_store, trades
from app.analysis import fundamental, signals, technical
from app.data import prices


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "")
    db.init_db()


def series(n=300, drift=0.002, noise=0.005, seed=1, end="2026-09-30", start_price=100.0):
    idx = pd.bdate_range(end=end, periods=n)
    rng = np.random.default_rng(seed)
    c = start_price * np.exp(np.cumsum(drift + rng.normal(0, noise, n)))
    o = np.r_[c[0], c[:-1]]
    return pd.DataFrame({"open": o, "high": np.maximum(o, c) * 1.003, "low": np.minimum(o, c) * 0.997, "close": c,
                         "adj_close": c, "volume": np.full(n, 1e6)}, index=idx)


def store(sym, df):
    with db.connect() as conn:
        conn.execute("INSERT OR IGNORE INTO tickers(symbol,name,market,sector,currency) VALUES(?,?,?,?,?)",
                     (sym, sym + " Inc", "SP500", "Tech", "USD"))
        prices.store_bars(conn, sym, df.rename(columns=str.title).rename(columns={"Adj_Close": "Adj Close"}).assign(**{"Adj Close": df["close"]}))


# ---- technical -------------------------------------------------------------
def test_technical_score_direction_and_warmup():
    up = technical.score_series(technical.indicators(series(drift=0.002)))
    down = technical.score_series(technical.indicators(series(drift=-0.002)))
    assert up.iloc[:199].isna().all() and up.iloc[-1] > 0.3
    assert down.iloc[-1] < -0.3


def test_no_lookahead_in_scores():
    df = series(320)
    full = technical.score_series(technical.indicators(df))
    cut = technical.score_series(technical.indicators(df.iloc[:260]))
    assert np.isclose(full.iloc[259], cut.iloc[-1])   # score at day 260 identical with or without the future


# ---- trade rules -----------------------------------------------------------
def test_resolve_rules():
    h, l, c = np.array([101, 112, 103.0]), np.array([99, 99, 100.0]), np.array([100, 110, 101.0])
    assert trades.resolve("BUY", 95, 110, h, l, c) == (110, "target", 2)
    # both stop and target touched in the same bar -> stop assumed first
    assert trades.resolve("BUY", 100, 110, np.array([112.0]), np.array([99.0]), np.array([105.0]))[1] == "stop"
    assert trades.resolve("SELL", 105, 90, np.array([101.0, 102]), np.array([89.0, 95]), np.array([95.0, 96]))[1] == "target"
    flat = np.full(25, 100.0)
    assert trades.resolve("BUY", 90, 120, flat, flat, flat)[1:] == ("timeout", trades.MAX_HOLD)
    assert trades.resolve("BUY", 90, 120, flat[:5], flat[:5], flat[:5]) is None   # not enough bars: still open
    assert trades.pnl_pct("BUY", 100, 110) == pytest.approx(10 - 0.2)


# ---- fundamentals / signals -------------------------------------------------
def _fund(rev, ni, debt, fcf):
    return pd.DataFrame({"period_end": pd.date_range("2025-03-31", periods=len(rev), freq="QE"), "revenue": rev,
                         "net_income": ni, "total_debt": debt, "free_cash_flow": fcf})


def test_fundamental_scoring():
    good = fundamental.score(_fund([100, 110, 120, 135, 150], [15, 17, 19, 22, 26], [50] * 5, [10] * 5))
    bad = fundamental.score(_fund([150, 140, 130, 120, 100], [-5, -8, -10, -12, -15], [900] * 5, [-10] * 5))
    assert good.available and good.score > 0.4 and bad.score < -0.4
    assert not fundamental.score(_fund([100], [10], [5], [1])).available


def test_combine_renormalises_missing_components():
    w = signals.DEFAULT_WEIGHTS
    assert signals.combine(0.8, None, None, w) == pytest.approx(0.8)
    assert signals.combine(0.8, -0.2, None, w) == pytest.approx((0.8 * 0.5 - 0.2 * 0.3) / 0.8)


# ---- backtest --------------------------------------------------------------
def test_backtest_runs_and_splits_out_of_sample():
    frames = {f"S{i}": series(700, drift=0.0004, noise=0.012, seed=i, end="2026-09-30") for i in range(6)}
    r = backtest.run(frames=frames, threshold=0.5)
    assert r["all"]["trades"] > 20
    assert r["in_sample"]["trades"] > 0 and r["out_of_sample"]["trades"] > 0
    assert 0 <= r["all"]["win_rate"] <= 1 and r["all"]["worst_trade_pct"] < 0   # losses are reported, not hidden
    assert "Out-of-sample" in backtest.to_markdown(r)


# ---- engine, grading, API data ---------------------------------------------
def test_engine_grading_and_store():
    store("UPUP", series(drift=0.002, seed=2))
    store("DOWN", series(drift=-0.002, seed=3))
    out = engine.generate(top_n=5, threshold=0.35, now=datetime(2026, 10, 1, 8, 30, tzinfo=timezone.utc))
    by = {r["symbol"]: r for r in out}
    assert by["UPUP"]["action"] == "BUY" and by["DOWN"]["action"] == "SELL"
    assert by["UPUP"]["target"] > by["UPUP"]["entry"] > by["UPUP"]["stop"]
    assert by["DOWN"]["target"] < by["DOWN"]["entry"] < by["DOWN"]["stop"]
    assert engine.generate(now=datetime(2026, 10, 1, 9, tzinfo=timezone.utc)) == []   # open calls are not duplicated

    shown = recs_store.latest_batch()
    assert {r["symbol"] for r in shown} >= {"UPUP", "DOWN"} and shown[0]["reasons"] and "technical" in shown[0]["scores"]

    # future bars: UPUP rallies through its target, DOWN rallies through its stop
    for sym, mult in (("UPUP", 1.5), ("DOWN", 1.5)):
        last = prices.get_prices(sym)["close"].iloc[-1]
        fut = pd.DataFrame({"Open": [last], "High": [last * mult], "Low": [last * 0.999], "Close": [last * mult], "Adj Close": [last * mult],
                            "Volume": [1e6]}, index=pd.to_datetime(["2026-10-05"]))
        with db.connect() as conn:
            prices.store_bars(conn, sym, fut)
    grading.grade_open()
    hist = {r["symbol"]: r for r in recs_store.history()}
    assert hist["UPUP"]["outcome"]["status"] == "WIN" and hist["UPUP"]["outcome"]["pnlPct"] > 0
    assert hist["DOWN"]["outcome"]["status"] == "LOSS" and hist["DOWN"]["outcome"]["pnlPct"] < 0
    perf = recs_store.performance()
    assert perf["versions"][0]["version"] == "v1" and perf["accuracy"][0]["winRate"] == pytest.approx(0.5)


def test_position_size_is_capped():
    assert engine.size_pct(100, 0.1) == engine.MAX_POSITION_PCT        # tiny stop -> capped
    assert engine.size_pct(100, 10) == 2.5                              # 20% stop, 0.5% risk -> 2.5% position
