"""Daily price ingestion via yfinance. Incremental: only fetches bars newer than what we have."""
from __future__ import annotations

import logging
from datetime import date, timedelta

import pandas as pd

from .. import config, db

log = logging.getLogger(__name__)


def _last_dates(conn) -> dict[str, str]:
    return {r["symbol"]: r["d"] for r in conn.execute("SELECT symbol, MAX(date) d FROM prices GROUP BY symbol")}


def store_bars(conn, symbol: str, df: pd.DataFrame) -> int:
    """Upsert a DataFrame with columns Open/High/Low/Close/Adj Close/Volume and a date index."""
    if df is None or df.empty:
        return 0
    df = df.dropna(subset=["Close"])
    rows = [
        (symbol, idx.strftime("%Y-%m-%d"), float(r["Open"]), float(r["High"]), float(r["Low"]),
         float(r["Close"]), float(r.get("Adj Close", r["Close"])), float(r["Volume"]))
        for idx, r in df.iterrows()
    ]
    conn.executemany(
        "INSERT OR REPLACE INTO prices(symbol,date,open,high,low,close,adj_close,volume) VALUES(?,?,?,?,?,?,?,?)",
        rows,
    )
    return len(rows)


def update_prices(symbols: list[str], batch_size: int = 50) -> dict[str, int]:
    """Download new daily bars for symbols. Returns {symbol: rows_added}."""
    import yfinance as yf  # imported lazily so tests don't need it

    added: dict[str, int] = {}
    with db.connect() as conn:
        last = _last_dates(conn)
        for i in range(0, len(symbols), batch_size):
            batch = symbols[i:i + batch_size]
            starts = [last[s] for s in batch if s in last]
            if len(starts) == len(batch):  # all have history -> incremental
                start = (date.fromisoformat(min(starts)) + timedelta(days=1)).isoformat()
                kwargs = {"start": start}
            else:
                kwargs = {"period": config.PRICE_HISTORY_PERIOD}
            try:
                data = yf.download(batch, interval=config.PRICE_INTERVAL, group_by="ticker",
                                   auto_adjust=False, progress=False, threads=True, **kwargs)
            except Exception as e:
                log.error("Price batch failed (%s): %s", batch[:3], e)
                continue
            for sym in batch:
                try:
                    df = data[sym] if len(batch) > 1 else data
                    added[sym] = store_bars(conn, sym, df)
                except Exception as e:
                    log.warning("No price data for %s: %s", sym, e)
                    added[sym] = 0
    return added


def get_prices(symbol: str, limit: int | None = None) -> pd.DataFrame:
    with db.connect() as conn:
        q = "SELECT date, open, high, low, close, adj_close, volume FROM prices WHERE symbol=? ORDER BY date"
        df = pd.read_sql_query(q, conn, params=(symbol,), parse_dates=["date"]).set_index("date")
    return df.tail(limit) if limit else df
