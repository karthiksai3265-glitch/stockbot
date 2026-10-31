"""Fundamental score from quarterly statements: growth, profitability, leverage, cash flow.

Returns a score in -1..+1 plus plain-English notes. `available=False` when there is not enough data, so
the signal combiner can leave this component out instead of treating "unknown" as "neutral".

Caveat: free sources only provide a few recent quarters, so this score cannot be backtested honestly
(we would be using today's financials to judge past dates). The backtester therefore tests the technical
component only and says so.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd


@dataclass
class FundamentalScore:
    score: float = 0.0
    available: bool = False
    notes: list[str] = field(default_factory=list)


def _clip(x: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def score(df: pd.DataFrame) -> FundamentalScore:
    """df: rows from the fundamentals table (ascending by period_end)."""
    if df is None or len(df) < 2:
        return FundamentalScore(notes=["Not enough financial statement data"])
    df = df.sort_values("period_end").reset_index(drop=True)
    last = df.iloc[-1]
    parts: list[tuple[float, float]] = []  # (value, weight)
    notes: list[str] = []

    ref = df.iloc[-5] if len(df) >= 5 else df.iloc[0]
    label = "year-on-year" if len(df) >= 5 else "over the available period"
    if pd.notna(last["revenue"]) and pd.notna(ref["revenue"]) and ref["revenue"] > 0:
        g = last["revenue"] / ref["revenue"] - 1
        parts.append((_clip(g / 0.30), 0.35))
        notes.append(f"Sales {g * 100:+.0f}% {label}")

    if pd.notna(last["net_income"]) and pd.notna(last["revenue"]) and last["revenue"] > 0:
        m = last["net_income"] / last["revenue"]
        parts.append((_clip(m / 0.15), 0.25))
        notes.append(f"Net profit margin {m * 100:.0f}%")

    if pd.notna(last["total_debt"]) and pd.notna(last["revenue"]) and last["revenue"] > 0:
        lev = last["total_debt"] / (4 * last["revenue"])
        parts.append((1 - 2 * _clip(lev / 2, 0, 1), 0.2))
        notes.append("Low debt compared with sales" if lev < 0.5 else "Debt is high compared with sales" if lev > 1.2 else "Moderate debt")

    if pd.notna(last["free_cash_flow"]):
        parts.append((1.0 if last["free_cash_flow"] > 0 else -1.0, 0.2))
        notes.append("Positive free cash flow" if last["free_cash_flow"] > 0 else "Negative free cash flow")

    if not parts:
        return FundamentalScore(notes=["Not enough financial statement data"])
    wsum = sum(w for _, w in parts)
    return FundamentalScore(score=_clip(sum(v * w for v, w in parts) / wsum), available=True, notes=notes)
