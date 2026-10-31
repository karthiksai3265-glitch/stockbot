"""Grade logged recommendations against what the stock actually did (same rules as the backtest)."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from . import db, trades
from .data import prices as price_data

log = logging.getLogger("grading")


def grade_open() -> dict:
    """Update every OPEN recommendation: WIN/LOSS if it hit target, stop or timed out; else refresh unrealised P&L."""
    db.init_db()
    counts = {"closed": 0, "still_open": 0}
    with db.connect() as conn:
        rows = conn.execute("SELECT * FROM recommendations WHERE status='OPEN'").fetchall()
        for r in rows:
            df = price_data.get_prices(r["symbol"])
            fut = df[df.index > r["created"][:10]]          # bars after the recommendation date
            if fut.empty:
                counts["still_open"] += 1
                continue
            res = trades.resolve(r["action"], r["stop"], r["target"], fut["high"].to_numpy(), fut["low"].to_numpy(), fut["close"].to_numpy())
            if res is None:   # still running: mark to market
                pnl = trades.pnl_pct(r["action"], r["entry"], float(fut["close"].iloc[-1]))
                conn.execute("UPDATE recommendations SET pnl_pct=? WHERE id=?", (pnl, r["id"]))
                counts["still_open"] += 1
                continue
            exit_price, reason, used = res
            pnl = trades.pnl_pct(r["action"], r["entry"], exit_price)
            conn.execute("UPDATE recommendations SET status=?, pnl_pct=?, closed=? WHERE id=?",
                         ("WIN" if pnl > 0 else "LOSS", pnl, fut.index[used - 1].strftime("%Y-%m-%d"), r["id"]))
            counts["closed"] += 1
    log.info("Graded: %s", counts)
    return counts
