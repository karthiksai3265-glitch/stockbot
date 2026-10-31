"""Combine technical, fundamental and news-sentiment scores into one signal per stock."""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from .. import db
from ..data import fundamentals as fund_data
from . import fundamental, technical

DEFAULT_WEIGHTS = {"technical": 0.5, "fundamental": 0.3, "sentiment": 0.2}


@dataclass
class Signal:
    symbol: str
    technical: float
    fundamental: float
    sentiment: float
    combined: float
    entry: float
    atr: float
    last_date: str
    reasons: list[str] = field(default_factory=list)


def combine(tech: float, fund: float | None, sent: float | None, weights: dict[str, float]) -> float:
    """Weighted average over the components that actually have data (weights renormalised)."""
    comps = [(tech, weights["technical"])]
    if fund is not None:
        comps.append((fund, weights["fundamental"]))
    if sent is not None:
        comps.append((sent, weights["sentiment"]))
    w = sum(x for _, x in comps)
    return sum(v * x for v, x in comps) / w if w else 0.0


def sentiment_for(symbol: str, limit: int = 20) -> tuple[float | None, int]:
    with db.connect() as conn:
        rows = conn.execute("SELECT sentiment FROM news WHERE symbol=? AND sentiment IS NOT NULL ORDER BY id DESC LIMIT ?",
                            (symbol, limit)).fetchall()
    if len(rows) < 3:  # too few headlines to mean anything
        return None, len(rows)
    return sum(r["sentiment"] for r in rows) / len(rows), len(rows)


def build_signal(symbol: str, prices: pd.DataFrame, weights: dict[str, float] | None = None,
                 tech_w: dict[str, float] | None = None) -> Signal | None:
    weights = weights or DEFAULT_WEIGHTS
    if len(prices) < 210:
        return None
    ind = technical.indicators(prices)
    tech = technical.score_series(ind, tech_w).iloc[-1]
    if pd.isna(tech):
        return None
    fs = fundamental.score(fund_data.get_fundamentals(symbol))
    sent, n_news = sentiment_for(symbol)

    reasons = technical.explain(ind)
    reasons += fs.notes if fs.available else []
    if sent is not None:
        reasons.append("Recent headlines are " + ("mostly positive" if sent > 0.15 else "mostly negative" if sent < -0.15 else "mixed or neutral"))

    combined = combine(float(tech), fs.score if fs.available else None, sent, weights)
    last = ind.iloc[-1]
    return Signal(symbol, float(tech), fs.score, sent if sent is not None else 0.0, combined,
                  float(last["close"]), float(last["atr"]), ind.index[-1].strftime("%Y-%m-%d"), reasons)
