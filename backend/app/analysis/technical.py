"""Technical analysis from price bars.

`score_series` is fully vectorised and uses only data up to each bar, so the backtester and the live
engine share the exact same logic (no divergence, no look-ahead).

Score range -1 (strong downtrend) .. +1 (strong uptrend):
  45% trend    price vs 50-day average, 50-day vs 200-day average
  35% momentum 63-day (about 3 month) return
  20% RSI      small penalty when overbought (>70), small bonus when oversold (<30)
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def indicators(df: pd.DataFrame) -> pd.DataFrame:
    """df needs columns open, high, low, close (date index, ascending)."""
    c, h, l = df["close"], df["high"], df["low"]
    out = pd.DataFrame(index=df.index)
    out["open"], out["high"], out["low"], out["close"] = df["open"], h, l, c
    out["sma50"] = c.rolling(50).mean()
    out["sma200"] = c.rolling(200).mean()
    d = c.diff()
    up = d.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    out["rsi"] = (100 * up / (up + dn)).fillna(50)
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    out["atr"] = tr.ewm(alpha=1 / 14, adjust=False).mean()
    out["ret63"] = c.pct_change(63)
    return out


DEFAULT_TECH_WEIGHTS = {"trend": 0.45, "momentum": 0.35, "rsi": 0.20}


def score_series(ind: pd.DataFrame, w: dict[str, float] | None = None) -> pd.Series:
    """w: component weights (trend, momentum, rsi). The learning loop tunes these."""
    w = w or DEFAULT_TECH_WEIGHTS
    trend = 0.5 * np.sign(ind["close"] - ind["sma50"]) + 0.5 * np.sign(ind["sma50"] - ind["sma200"])
    mom = (ind["ret63"] / 0.20).clip(-1, 1)
    rsi_adj = ((30 - ind["rsi"]) / 15).clip(0, 1) - ((ind["rsi"] - 70) / 15).clip(0, 1)
    score = (w["trend"] * trend + w["momentum"] * mom + w["rsi"] * rsi_adj).clip(-1, 1)
    return score.where(ind["sma200"].notna())  # need 200 bars of history


def explain(ind: pd.DataFrame) -> list[str]:
    """Plain-English reasons from the latest bar."""
    r = ind.iloc[-1]
    out = []
    if r["close"] > r["sma50"] > r["sma200"]:
        out.append("Price is above its 50-day and 200-day averages (uptrend)")
    elif r["close"] < r["sma50"] < r["sma200"]:
        out.append("Price is below its 50-day and 200-day averages (downtrend)")
    elif r["close"] > r["sma50"]:
        out.append("Price is above its 50-day average but the longer trend is mixed")
    else:
        out.append("Price is below its 50-day average but the longer trend is mixed")
    if pd.notna(r["ret63"]):
        out.append(f"3-month return {r['ret63'] * 100:+.1f}%")
    if r["rsi"] > 70:
        out.append(f"RSI {r['rsi']:.0f}: overbought, a pullback is more likely")
    elif r["rsi"] < 30:
        out.append(f"RSI {r['rsi']:.0f}: oversold, a bounce is more likely")
    return out
