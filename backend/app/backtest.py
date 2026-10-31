"""Walk-forward backtester for the TECHNICAL score.  Usage (from backend/):

    python -m app.backtest                 # all tickers in the database
    python -m app.backtest --limit 50 --threshold 0.5

What it does: for every ticker and day it computes the technical score using only data up to that day. If the
score is strong enough, it enters at the NEXT day's open (no look-ahead), with ATR-based stop and target, and
exits via shared rules in trades.py. Results are split into an earlier in-sample period and a later
out-of-sample period; trust the out-of-sample numbers, not the in-sample ones.

What it does NOT do: fundamentals and news are not backtested, because free data sources cannot give us what
was known on past dates. Survivorship bias also exists (today's index members only). Treat results as
optimistic.
"""
from __future__ import annotations

import argparse
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from . import db, trades
from .analysis import technical
from .data import prices as price_data
from .data import universe

log = logging.getLogger("backtest")
STOP_ATR, TARGET_ATR = 2.0, 3.0
POSITION_FRACTION = 0.05  # equity fraction per trade in the approximate equity curve


def backtest_symbol(df: pd.DataFrame, threshold: float, stop_atr: float = STOP_ATR, target_atr: float = TARGET_ATR,
                    tech_w: dict | None = None, ind: pd.DataFrame | None = None) -> list[dict]:
    """ind: pre-computed indicators (the learning loop reuses them across many candidates for speed)."""
    ind = ind if ind is not None else technical.indicators(df)
    score = technical.score_series(ind, tech_w).to_numpy()
    o, h, l, c = (ind[k].to_numpy() for k in ("open", "high", "low", "close"))
    atr = ind["atr"].to_numpy()
    dates = ind.index
    out: list[dict] = []
    i, n = 0, len(ind)
    while i < n - 2:
        s = score[i]
        if np.isnan(s) or abs(s) < threshold or np.isnan(atr[i]) or atr[i] <= 0:
            i += 1
            continue
        side = "BUY" if s > 0 else "SELL"
        j = i + 1                       # enter at next open
        entry = o[j]
        if side == "BUY":
            stop, target = entry - stop_atr * atr[i], entry + target_atr * atr[i]
        else:
            stop, target = entry + stop_atr * atr[i], entry - target_atr * atr[i]
        res = trades.resolve(side, stop, target, h[j:], l[j:], c[j:])
        if res is None:  # not enough future bars: trade still open, exclude from stats
            break
        exit_price, reason, used = res
        out.append({"date": dates[j].strftime("%Y-%m-%d"), "exit_date": dates[j + used - 1].strftime("%Y-%m-%d"),
                    "side": side, "reason": reason, "pnl": trades.pnl_pct(side, entry, exit_price)})
        i = j + used                    # one trade at a time per symbol
    return out


def metrics(tr: list[dict], years: float | None = None) -> dict:
    if not tr:
        return {"trades": 0}
    p = np.array([t["pnl"] for t in tr])
    wins, losses = p[p > 0], p[p <= 0]
    eq, peak, mdd = 1.0, 1.0, 0.0
    for t in sorted(tr, key=lambda t: t["exit_date"]):
        eq *= 1 + POSITION_FRACTION * t["pnl"] / 100
        peak = max(peak, eq)
        mdd = max(mdd, (peak - eq) / peak)
    return {
        "trades": len(p), "win_rate": float((p > 0).mean()), "avg_trade_pct": float(p.mean()),
        "avg_win_pct": float(wins.mean()) if len(wins) else 0.0, "avg_loss_pct": float(losses.mean()) if len(losses) else 0.0,
        "worst_trade_pct": float(p.min()), "best_trade_pct": float(p.max()),
        "profit_factor": float(wins.sum() / -losses.sum()) if losses.sum() < 0 else None,
        "approx_total_return_pct": (eq - 1) * 100, "approx_max_drawdown_pct": mdd * 100,
        "stop_exits": int(sum(t["reason"] == "stop" for t in tr)), "target_exits": int(sum(t["reason"] == "target" for t in tr)),
        "timeouts": int(sum(t["reason"] == "timeout" for t in tr)),
    }


def run(symbols: list[str] | None = None, threshold: float = 0.5, oos_fraction: float = 0.4, frames: dict | None = None) -> dict:
    """frames: optional {symbol: DataFrame} (used by tests); otherwise read from the database."""
    if frames is None:
        db.init_db()
        symbols = symbols or universe.active_symbols()
        frames = {s: price_data.get_prices(s) for s in symbols}
    all_tr: list[dict] = []
    bh: list[float] = []
    for sym, df in frames.items():
        if len(df) < 260:
            continue
        for t in backtest_symbol(df, threshold):
            all_tr.append({**t, "symbol": sym})
        bh.append(float(df["close"].iloc[-1] / df["close"].iloc[200] - 1) * 100)
    if not all_tr:
        return {"error": "No trades. Load more price history first (need 260+ daily bars per ticker)."}
    dates = sorted(t["date"] for t in all_tr)
    split = dates[int(len(dates) * (1 - oos_fraction))]
    ins = [t for t in all_tr if t["date"] < split]
    oos = [t for t in all_tr if t["date"] >= split]
    return {
        "threshold": threshold, "stop_atr": STOP_ATR, "target_atr": TARGET_ATR, "max_hold_days": trades.MAX_HOLD,
        "cost_per_side_pct": trades.COST_PER_SIDE * 100, "split_date": split, "symbols_tested": len(bh),
        "all": metrics(all_tr), "in_sample": metrics(ins), "out_of_sample": metrics(oos),
        "buy_and_hold_avg_return_pct": float(np.mean(bh)) if bh else None,
        "caveats": ["Technical score only; fundamentals/news not backtested (no point-in-time data).",
                    "Survivorship bias: only current index members are tested.",
                    "Approximate equity curve ignores overlapping trades; use for relative comparison only."],
    }


def to_markdown(r: dict) -> str:
    if "error" in r:
        return f"# Backtest\n\n{r['error']}\n"
    def row(name, m):
        if not m.get("trades"):
            return f"| {name} | 0 | – | – | – | – | – |"
        return (f"| {name} | {m['trades']} | {m['win_rate'] * 100:.0f}% | {m['avg_trade_pct']:+.2f}% | "
                f"{m['avg_win_pct']:+.2f}% / {m['avg_loss_pct']:+.2f}% | {m['approx_total_return_pct']:+.1f}% | {m['approx_max_drawdown_pct']:.1f}% |")
    lines = ["# Backtest report (technical score)", "",
             f"Tickers tested: {r['symbols_tested']} · entry threshold {r['threshold']} · stop {r['stop_atr']}x ATR · target {r['target_atr']}x ATR · "
             f"max hold {r['max_hold_days']} days · costs {r['cost_per_side_pct']:.1f}% per side", "",
             f"Out-of-sample period starts {r['split_date']}. **Trust the out-of-sample row, not in-sample.**", "",
             "| Period | Trades | Win rate | Avg trade | Avg win / avg loss | Approx total return | Max drawdown |", "|---|---|---|---|---|---|---|",
             row("All", r["all"]), row("In-sample", r["in_sample"]), row("Out-of-sample", r["out_of_sample"]), "",
             f"Average buy-and-hold return of the same tickers over the same data: {r['buy_and_hold_avg_return_pct']:+.1f}%", "",
             "## Caveats", *[f"- {c}" for c in r["caveats"]], ""]
    return "\n".join(lines)


def save_baseline(result: dict, weights: dict | None = None) -> None:
    """Record the backtest against model version v1 so the website can show it."""
    from . import model
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO model_versions(version,created,weights,backtest,note,active) VALUES('v1',?,?,?,?,1) "
            "ON CONFLICT(version) DO UPDATE SET backtest=excluded.backtest",
            (datetime.now(timezone.utc).isoformat(), json.dumps(weights or model.DEFAULT_PARAMS), json.dumps(result),
             model.BASELINE_NOTE))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int)
    ap.add_argument("--threshold", type=float, default=0.5)
    a = ap.parse_args()
    db.init_db()
    syms = universe.active_symbols()[: a.limit]
    res = run(syms, a.threshold)
    md = to_markdown(res)
    print(md)
    Path("reports").mkdir(exist_ok=True)
    Path("reports/backtest.md").write_text(md)
    if "error" not in res:
        save_baseline(res)
