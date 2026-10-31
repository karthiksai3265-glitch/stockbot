"""Paper portfolio: sizing, risk limits, exits, kill switch, daily loss halt."""
import json

import numpy as np
import pandas as pd
import pytest

from app import config, db, portfolio
from app.data import prices

BATCH = "2026-10-01T08:30"
CREATED = "2026-10-01T08:30:00+00:00"


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "p.db"))
    db.init_db()


def flat_bars(price=100.0, n=5, end="2026-10-01"):
    idx = pd.bdate_range(end=end, periods=n)
    return pd.DataFrame({"Open": price, "High": price * 1.001, "Low": price * 0.999, "Close": price, "Adj Close": price, "Volume": 1e6}, index=idx)


def add_stock(sym, sector="Tech", price=100.0):
    with db.connect() as conn:
        conn.execute("INSERT INTO tickers(symbol,name,market,sector,currency) VALUES(?,?,?,?,?)", (sym, sym, "SP500", sector, "USD"))
        prices.store_bars(conn, sym, flat_bars(price))


def add_rec(sym, action="BUY", size=5.0, price=100.0, strength=0.7, created=CREATED, batch=BATCH):
    stop, target = (price * 0.94, price * 1.09) if action == "BUY" else (price * 1.06, price * 0.91)
    with db.connect() as conn:
        conn.execute("INSERT INTO recommendations(batch,created,symbol,action,entry,target,stop,size_pct,strength,reasons,combined,model_version)"
                     " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (batch, created, sym, action, price, target, stop, size, strength, json.dumps(["t"]), strength, "v1"))


def add_bar(sym, date, high, low, close):
    df = pd.DataFrame({"Open": close, "High": high, "Low": low, "Close": close, "Adj Close": close, "Volume": 1e6}, index=pd.to_datetime([date]))
    with db.connect() as conn:
        prices.store_bars(conn, sym, df)


def test_sector_cap_and_position_cap():
    for i in range(7):
        add_stock(f"T{i}", sector="Tech")
        add_rec(f"T{i}", size=5.0, strength=0.9 - i * 0.01)
    out = portfolio.sync("2026-10-01")
    snap = portfolio.snapshot()
    assert out["opened"] == 5                                    # 5 x 5% = 25% sector cap; the other two are skipped
    assert any("no room" in s for s in out["skipped"])
    assert snap["cash"] == pytest.approx(100000 - 5 * 5000)
    assert snap["equity"][-1]["value"] == pytest.approx(75000 + 25000 * 0.998)   # flat prices: only the 0.2% round-trip cost shows


def test_two_sectors_and_no_duplicates():
    add_stock("A", "Tech"); add_stock("B", "Energy")
    add_rec("A"); add_rec("B")
    assert portfolio.sync("2026-10-01")["opened"] == 2
    assert portfolio.sync("2026-10-01")["opened"] == 0           # same recommendations are not traded twice
    assert len(portfolio.snapshot()["positions"]) == 2


def test_target_hit_closes_with_profit():
    add_stock("A"); add_rec("A", price=100.0)
    portfolio.sync("2026-10-01")
    add_bar("A", "2026-10-05", high=112, low=100, close=111)     # target is 109
    out = portfolio.sync("2026-10-05")
    snap = portfolio.snapshot()
    assert out["closed"] == 1 and snap["positions"] == []
    assert snap["closed_trades"] == 1 and snap["win_rate"] == 1.0 and snap["realised_pnl"] > 0
    assert snap["cash"] > 100000


def test_stop_hit_closes_with_loss():
    add_stock("A"); add_rec("A", price=100.0)
    portfolio.sync("2026-10-01")
    add_bar("A", "2026-10-05", high=100, low=90, close=92)       # stop is 94
    portfolio.sync("2026-10-05")
    snap = portfolio.snapshot()
    assert snap["win_rate"] == 0.0 and snap["realised_pnl"] < 0 and snap["cash"] < 100000


def test_sell_signal_exits_position():
    add_stock("A"); add_rec("A")
    portfolio.sync("2026-10-01")
    add_rec("A", action="SELL", created="2026-10-03T08:30:00+00:00", batch="2026-10-03T08:30")
    add_bar("A", "2026-10-03", high=101, low=99, close=100.5)
    out = portfolio.sync("2026-10-03")
    assert out["closed"] == 1 and portfolio.snapshot()["positions"] == []


def test_kill_switch_blocks_new_entries():
    add_stock("A"); add_rec("A")
    portfolio.set_paused(True)
    out = portfolio.sync("2026-10-01")
    assert out["opened"] == 0 and out["paused"] and portfolio.snapshot()["paused"]
    portfolio.set_paused(False)
    assert portfolio.sync("2026-10-01")["opened"] == 1


def test_daily_loss_limit_halts_entries():
    add_stock("A"); add_rec("A")
    portfolio.sync("2026-09-30")                                  # creates the account (nothing to open that matters)
    with db.connect() as conn:
        conn.execute("DELETE FROM paper_trades"); conn.execute("UPDATE paper_state SET cash=100000")
        conn.execute("INSERT OR REPLACE INTO paper_equity(date,value) VALUES('2026-09-29',110000)")   # yesterday was 110k, now 100k (-9%)
        conn.execute("DELETE FROM paper_equity WHERE date='2026-09-30'")
    out = portfolio.sync("2026-10-01")
    assert out["halted"] and out["opened"] == 0
