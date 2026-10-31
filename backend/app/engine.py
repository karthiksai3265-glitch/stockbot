"""Recommendation engine: scores every ticker, picks the strongest signals, logs them to the database.

    python -m app.engine            # generate now
    python -m app.engine --top 8 --threshold 0.4

Stops and targets are ATR-based (stop 2x ATR, target 3x ATR, same as the backtest). Position size is risk-based
(0.5% of the portfolio at risk per trade) and capped at 5%. "Strength" is how strong the combined signal is; it is
NOT a probability of winning.
"""
from __future__ import annotations

import argparse
import json
import logging
from datetime import datetime, timedelta, timezone

import pandas as pd

from . import db, model
from .analysis import signals
from .backtest import STOP_ATR, TARGET_ATR
from .data import prices as price_data
from .data import universe

log = logging.getLogger("engine")
RISK_PER_TRADE = 0.005   # fraction of portfolio risked per trade
MAX_POSITION_PCT = 5.0
WINDOW = "Next market open: use a limit order at or near the entry price"


def size_pct(entry: float, atr: float, stop_atr: float = STOP_ATR) -> float:
    stop_frac = stop_atr * atr / entry
    return round(min(MAX_POSITION_PCT, RISK_PER_TRADE / stop_frac * 100), 1) if stop_frac > 0 else 0.0


def levels(side: str, entry: float, atr: float, stop_atr: float = STOP_ATR, target_atr: float = TARGET_ATR) -> tuple[float, float]:
    if side == "BUY":
        return entry + target_atr * atr, entry - stop_atr * atr   # target, stop
    return entry - target_atr * atr, entry + stop_atr * atr


def generate(top_n: int = 5, threshold: float | None = None, now: datetime | None = None) -> list[dict]:
    """Uses the ACTIVE model version's settings (blend weights, technical weights, threshold, stop/target)."""
    db.init_db()
    now = now or datetime.now(timezone.utc)
    version, params = model.active()
    weights, tech_w = params["blend"], params["tech"]
    stop_atr, target_atr = params["stop_atr"], params["target_atr"]
    threshold = params["threshold"] if threshold is None else threshold
    with db.connect() as conn:
        open_syms = {r["symbol"] for r in conn.execute("SELECT symbol FROM recommendations WHERE status='OPEN'")}

    cands: list[signals.Signal] = []
    for sym in universe.active_symbols():
        if sym in open_syms:
            continue   # already have an open call on it
        df = price_data.get_prices(sym)
        sig = signals.build_signal(sym, df, weights, tech_w)
        if sig and abs(sig.combined) >= threshold and sig.atr > 0:
            cands.append(sig)
    if not cands:
        log.info("No signals above threshold %.2f", threshold)
        return []

    newest = max(pd.Timestamp(c.last_date) for c in cands)
    cands = [c for c in cands if pd.Timestamp(c.last_date) >= newest - timedelta(days=5)]   # drop stale data
    cands.sort(key=lambda c: abs(c.combined), reverse=True)
    chosen = cands[:top_n]

    batch = now.strftime("%Y-%m-%dT%H:%M")
    out = []
    with db.connect() as conn:
        for s in chosen:
            side = "BUY" if s.combined > 0 else "SELL"
            target, stop = levels(side, s.entry, s.atr, stop_atr, target_atr)
            row = (batch, now.isoformat(), s.symbol, side, s.entry, target, stop, size_pct(s.entry, s.atr, stop_atr),
                   min(1.0, abs(s.combined)), WINDOW, json.dumps(s.reasons), s.technical, s.fundamental, s.sentiment,
                   s.combined, version)
            conn.execute("INSERT INTO recommendations(batch,created,symbol,action,entry,target,stop,size_pct,strength,window,"
                         "reasons,score_tech,score_fund,score_sent,combined,model_version) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", row)
            out.append({"symbol": s.symbol, "action": side, "entry": s.entry, "target": target, "stop": stop, "combined": s.combined})
    log.info("Generated %d recommendations (batch %s, model %s)", len(out), batch, version)
    return out


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=5)
    ap.add_argument("--threshold", type=float, default=None, help="default: the active model's threshold")
    a = ap.parse_args()
    for r in generate(a.top, a.threshold):
        print(r)
