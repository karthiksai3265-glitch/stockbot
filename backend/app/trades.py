"""Shared trade-outcome logic, used by BOTH the backtester and the grader so results are comparable.

Rules (deliberately conservative):
- If a bar touches both stop and target, assume the stop was hit first.
- Trades time out after MAX_HOLD bars and close at that bar's close.
- Costs: COST_PER_SIDE (0.1%) on entry and exit to cover commission and slippage.
"""
from __future__ import annotations

import numpy as np

MAX_HOLD = 20
COST_PER_SIDE = 0.001


def pnl_pct(side: str, entry: float, exit_price: float) -> float:
    gross = (exit_price - entry) / entry if side == "BUY" else (entry - exit_price) / entry
    return (gross - 2 * COST_PER_SIDE) * 100


def resolve(side: str, stop: float, target: float, highs: np.ndarray, lows: np.ndarray, closes: np.ndarray,
            max_hold: int = MAX_HOLD) -> tuple[float, str, int] | None:
    """Walk forward bar by bar. Returns (exit_price, reason, bars_used) or None if still open.

    reason is 'stop', 'target' or 'timeout'.
    """
    n = min(len(highs), max_hold)
    for i in range(n):
        if side == "BUY":
            if lows[i] <= stop:
                return stop, "stop", i + 1
            if highs[i] >= target:
                return target, "target", i + 1
        else:
            if highs[i] >= stop:
                return stop, "stop", i + 1
            if lows[i] <= target:
                return target, "target", i + 1
    if len(highs) >= max_hold:
        return float(closes[max_hold - 1]), "timeout", max_hold
    return None
