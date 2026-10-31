"""Demo mode: fill a SEPARATE database with synthetic data so the website can be seen fully populated.

    python -m app.demo                # builds backend/demo.db
    python -m app.serve --demo        # builds it if missing, then serves it

Everything is fictional: made-up companies ("DEMO1"...), random-walk prices, invented financials and headlines. The real
engine, grading, paper portfolio, backtester and learning loop then run over that data day by day, so the pages look and
behave exactly as they will with real data. Nothing here says anything about real markets, and the website labels it.
"""
from __future__ import annotations

import argparse
import logging
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from . import backtest, config, db, engine, grading, learn, portfolio
from .data import news as news_data
from .data import prices as price_data

log = logging.getLogger("demo")
DEMO_DB = str(config.BASE_DIR / "demo.db")
N_BARS = 900
REPLAY_DAYS = 60          # the last 60 trading days are replayed one day at a time through the real engine

SECTORS = ["Information Technology", "Financials", "Health Care", "Energy", "Consumer Discretionary", "Materials", "Industrials", "Utilities"]
CATALOG = [("SP500", "USD", "", 10), ("ASX200", "AUD", ".AX", 6), ("NIFTY50", "INR", ".NS", 6)]
NAME_A = ["Northwind", "Harbour", "Summit", "Granite", "Lumen", "Cobalt", "Meridian", "Alder", "Quarry", "Beacon", "Tidewater", "Falcon",
          "Juniper", "Atlas", "Ember", "Cedar", "Orchid", "Pioneer", "Vertex", "Kestrel", "Marlow", "Sterling"]
NAME_B = ["Systems", "Financial", "Health", "Energy", "Retail", "Resources", "Industries", "Power"]


def _series(seed: int, tilt: float) -> pd.DataFrame:
    r = np.random.default_rng(seed)
    vol = r.uniform(0.010, 0.020)
    drift = np.concatenate([np.full(120, r.normal(0.0004, 0.0012)) for _ in range(N_BARS // 120 + 1)])[:N_BARS]
    drift[-110:] += tilt                                    # make sure the last few months trend, so signals appear
    c = r.uniform(30, 300) * np.exp(np.cumsum(drift + r.normal(0, vol, N_BARS)))
    o = np.r_[c[0], c[:-1]] * (1 + r.normal(0, 0.002, N_BARS))
    hi = np.maximum(o, c) * (1 + np.abs(r.normal(0, 0.004, N_BARS)))
    lo = np.minimum(o, c) * (1 - np.abs(r.normal(0, 0.004, N_BARS)))
    idx = pd.bdate_range(end=date.today(), periods=N_BARS)
    return pd.DataFrame({"Open": o, "High": hi, "Low": lo, "Close": c, "Adj Close": c, "Volume": r.integers(500_000, 5_000_000, N_BARS).astype(float)}, index=idx)


def _quarter_ends(n: int) -> list[date]:
    out, d = [], date.today()
    for y in range(d.year, d.year - 3, -1):
        for m, day in ((12, 31), (9, 30), (6, 30), (3, 31)):
            q = date(y, m, day)
            if q < d:
                out.append(q)
    return out[:n][::-1]


def _fundamentals(conn, sym: str, r: np.random.Generator, growing: bool) -> None:
    base = r.uniform(2e9, 40e9)
    g = r.uniform(0.02, 0.06) if growing else r.uniform(-0.04, 0.005)
    for i, q in enumerate(_quarter_ends(5)):
        rev = base * (1 + g) ** i
        margin = (r.uniform(0.12, 0.22) if growing else r.uniform(0.01, 0.07))
        conn.execute("INSERT OR REPLACE INTO fundamentals(symbol,period_end,revenue,gross_profit,operating_income,net_income,total_debt,cash,"
                     "operating_cash_flow,free_cash_flow,shares_outstanding) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                     (sym, q.isoformat(), rev, rev * 0.45, rev * margin * 1.3, rev * margin, base * r.uniform(0.3, 1.4), base * 0.25,
                      rev * (margin + 0.04), rev * (margin + 0.02) * (1 if growing else 0.3), 1e9))


HEAD_POS = ["{n} beats quarterly estimates and raises full-year outlook", "{n} wins large multi-year contract", "Analysts upgrade {n} on strong growth"]
HEAD_NEG = ["{n} cuts guidance as demand weakens", "{n} faces regulator probe into pricing", "Analysts downgrade {n} after weak results"]
HEAD_NEU = ["{n} announces routine management update", "{n} to present at industry conference", "Analysts split on {n} valuation"]


def _news(conn, sym: str, name: str, r: np.random.Generator, mood: int) -> None:
    pools = [HEAD_POS, HEAD_NEG, HEAD_NEU]
    items = []
    for i in range(6):
        pool = pools[mood] if r.random() < 0.6 else pools[int(r.integers(0, 3))]
        items.append({"title": pool[int(r.integers(0, 3))].format(n=name), "url": f"#demo-{sym}-{i}", "publisher": "Demo Wire",
                      "published": (date.today() - timedelta(days=int(i * 2 + r.integers(0, 2)))).isoformat()})
    news_data.store_news(conn, sym, items)


def seed(path: str | None = None, force: bool = True) -> dict:
    """Build the demo database at `path` (default backend/demo.db) and replay the last days through the real pipeline."""
    path = path or DEMO_DB
    if force and Path(path).exists():
        Path(path).unlink()
    config.DB_PATH = path
    saved_key, config.ANTHROPIC_API_KEY = config.ANTHROPIC_API_KEY, ""      # never call a paid API for demo headlines
    db.init_db()

    series: dict[str, pd.DataFrame] = {}
    k = 0
    with db.connect() as conn:
        for market, cur, suffix, n in CATALOG:
            for j in range(n):
                k += 1
                sym = f"DEMO{k}{suffix}"
                name = f"{NAME_A[(k * 3) % len(NAME_A)]} {NAME_B[k % len(NAME_B)]} (demo)"
                sector = SECTORS[k % len(SECTORS)]
                r = np.random.default_rng(1000 + k)
                tilt = (0.0016, -0.0013, 0.0004)[k % 3]
                conn.execute("INSERT INTO tickers(symbol,name,market,sector,currency) VALUES(?,?,?,?,?)", (sym, name, market, sector, cur))
                series[sym] = _series(2000 + k, tilt)
                _fundamentals(conn, sym, r, growing=tilt > 0)
                _news(conn, sym, name.replace(" (demo)", ""), r, mood=0 if tilt > 0 else 1 if tilt < 0 else 2)
    db.set_meta("demo", "1")

    # Everything except the replay window goes in first; then replay one trading day at a time.
    start = N_BARS - REPLAY_DAYS
    with db.connect() as conn:
        for sym, df in series.items():
            price_data.store_bars(conn, sym, df.iloc[:start])
    dates = next(iter(series.values())).index
    for i in range(start, N_BARS):
        with db.connect() as conn:
            for sym, df in series.items():
                price_data.store_bars(conn, sym, df.iloc[i:i + 1])
        day = dates[i]
        grading.grade_open()
        if (i - start) % 5 == 0:
            engine.generate(top_n=4, threshold=0.35, now=datetime(day.year, day.month, day.day, 8, 30, tzinfo=timezone.utc))
        portfolio.sync(day.strftime("%Y-%m-%d"))

    frames = {s: price_data.get_prices(s) for s in series}
    bt = backtest.run(list(frames), 0.5, frames=frames)
    if "error" not in bt:
        backtest.save_baseline(bt)
    learn.run()                                                              # real learning cycle on the demo prices
    config.ANTHROPIC_API_KEY = saved_key
    with db.connect() as conn:
        out = {"tickers": len(series), "recommendations": conn.execute("SELECT COUNT(*) c FROM recommendations").fetchone()["c"],
               "graded": conn.execute("SELECT COUNT(*) c FROM recommendations WHERE status IN ('WIN','LOSS')").fetchone()["c"],
               "open_positions": conn.execute("SELECT COUNT(*) c FROM paper_trades WHERE status='OPEN'").fetchone()["c"]}
    log.info("Demo database ready at %s: %s", path, out)
    return out


def ensure(path: str | None = None) -> None:
    """Use the existing demo database if there is one, otherwise build it."""
    path = path or DEMO_DB
    config.DB_PATH = path
    if Path(path).exists() and db.get_meta("demo") == "1":
        return
    seed(path)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DEMO_DB)
    print(seed(ap.parse_args().db))
