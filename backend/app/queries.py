"""Read-only database queries shared by the website server (and anything else that needs plain data)."""
from __future__ import annotations

from . import db


def _rows(sql: str, params: tuple = ()) -> list[dict]:
    db.init_db()
    with db.connect() as conn:
        return [dict(r) for r in conn.execute(sql, params)]


def tickers() -> list[dict]:
    return _rows("SELECT symbol, name, market, sector, currency FROM tickers WHERE active=1 ORDER BY symbol")


def prices(symbol: str, days: int = 250) -> list[dict]:
    rows = _rows("SELECT date, open, high, low, close, volume FROM prices WHERE symbol=? ORDER BY date DESC LIMIT ?",
                 (symbol.upper(), max(1, min(days, 1500))))
    return list(reversed(rows))


def fundamentals(symbol: str) -> list[dict]:
    return _rows("SELECT * FROM fundamentals WHERE symbol=? ORDER BY period_end", (symbol.upper(),))


def news(symbol: str | None = None, limit: int = 30) -> list[dict]:
    limit = max(1, min(limit, 200))
    if symbol:
        return _rows("SELECT symbol, published, title, publisher, url, sentiment FROM news WHERE symbol=? ORDER BY id DESC LIMIT ?", (symbol.upper(), limit))
    return _rows("SELECT symbol, published, title, publisher, url, sentiment FROM news ORDER BY id DESC LIMIT ?", (limit,))


def data_status() -> dict:
    """What the database holds and how fresh it is (shown in the website header)."""
    db.init_db()
    with db.connect() as conn:
        n_t = conn.execute("SELECT COUNT(*) c FROM tickers WHERE active=1").fetchone()["c"]
        last_px = conn.execute("SELECT MAX(date) d FROM prices").fetchone()["d"]
        last_batch = conn.execute("SELECT MAX(created) c FROM recommendations").fetchone()["c"]
        runs = [dict(r) for r in conn.execute("SELECT step, ok, finished, detail FROM pipeline_runs ORDER BY id DESC LIMIT 8")]
    return {"tickers": n_t, "last_price_date": last_px, "last_recommendation": last_batch, "recent_runs": runs,
            "demo": db.get_meta("demo") == "1"}
