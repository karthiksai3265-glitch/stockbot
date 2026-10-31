"""Self-improvement loop.   python -m app.learn           one learning cycle
                           python -m app.learn rollback  go back to the previous model version

What "self-adapting" means here, and what it does not:
- Each cycle tries small changes to the ACTIVE model (technical weights, entry threshold, stop and target
  distance) and, once enough live calls are graded, to the blend of technical / fundamental / news scores.
- Candidates are compared on older data only ("in-sample"). The single best one is then judged ONCE on newer
  data it was not tuned on ("out-of-sample"). Trying every candidate out-of-sample would just overfit that data.
- It replaces the active model only if it passes every check in `Gate`: enough trades, a positive average trade
  after costs, a real improvement, statistical evidence (z-score), no worse drawdown, and a 30-day cooldown since
  the last change.
- Every attempt, accepted or rejected, goes to the model_log table and the website. Old versions are kept.
- If a new version's live (graded) record turns out worse than its parent's, it is rolled back automatically.

Limits: out-of-sample data is re-used from cycle to cycle, trades in different stocks are correlated, and the
backtest only covers the technical score. The gates make wrong promotions rarer, not impossible.
"""
from __future__ import annotations

import argparse
import copy
import itertools
import json
import logging
import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import numpy as np

from . import backtest, db, model
from .analysis import technical
from .data import prices as price_data
from .data import universe

log = logging.getLogger("learn")
TECH_KEYS = ("trend", "momentum", "rsi")


@dataclass
class Gate:
    min_is_trades: int = 150        # a candidate needs this many in-sample trades to be considered
    min_oos_trades: int = 150       # out-of-sample trades needed for a promotion
    min_improvement: float = 0.05   # percentage points of average trade (after costs) over the active model
    z_min: float = 1.28             # about 90% one-sided evidence that the improvement is not luck
    require_positive: bool = True   # candidate's out-of-sample average trade must be > 0 after costs
    max_dd_ratio: float = 1.25      # candidate drawdown may be at most 25% worse than the active model's
    cooldown_days: int = 30         # at most one promotion per month
    min_graded: int = 60            # graded live calls needed before the blend is tuned
    blend_fit_min: int = 15
    blend_min_taken: int = 20
    blend_min_improvement: float = 0.10
    live_check_min: int = 40        # graded calls per version before the live rollback check applies


# --------------------------------------------------------------------------- statistics
def _mean_std(vals: list[float]) -> tuple[int, float, float]:
    a = np.asarray(vals, dtype=float)
    n = len(a)
    return n, (float(a.mean()) if n else 0.0), (float(a.std(ddof=1)) if n > 1 else 0.0)


def z_score(cand: list[float], active: list[float]) -> float:
    n1, m1, s1 = _mean_std(cand)
    n2, m2, s2 = _mean_std(active)
    if n1 < 2 or n2 < 2:
        return 0.0
    se = math.sqrt(s1 ** 2 / n1 + s2 ** 2 / n2)
    return (m1 - m2) / se if se > 0 else 0.0


# --------------------------------------------------------------------------- data + evaluation
def prepare(frames: dict) -> dict:
    """{symbol: (prices, indicators)} for tickers with enough history. Indicators are computed once."""
    return {s: (df, technical.indicators(df)) for s, df in frames.items() if len(df) >= 260}


def split_date(prepared: dict, oos_fraction: float = 0.4) -> str:
    dates = sorted({d for _, ind in prepared.values() for d in ind.index})
    return dates[int(len(dates) * (1 - oos_fraction))].strftime("%Y-%m-%d")


def evaluate(prepared: dict, params: dict, split: str) -> tuple[list[dict], list[dict]]:
    """Backtest one parameter set. In-sample trades must exit before the split; out-of-sample ones start on or
    after it. Trades that straddle the split are dropped so the two sets never share information."""
    ins, oos = [], []
    for sym, (df, ind) in prepared.items():
        for t in backtest.backtest_symbol(df, params["threshold"], params["stop_atr"], params["target_atr"], params["tech"], ind=ind):
            t["symbol"] = sym
            if t["exit_date"] < split:
                ins.append(t)
            elif t["date"] >= split:
                oos.append(t)
    return ins, oos


def candidates(params: dict) -> list[tuple[str, dict]]:
    """Small single-step changes around the active model."""
    out: list[tuple[str, dict]] = []
    w = params["tech"]
    for a, b in itertools.permutations(TECH_KEYS, 2):
        if w[a] - 0.1 >= 0.05 - 1e-9:
            c = copy.deepcopy(params)
            c["tech"][a], c["tech"][b] = round(w[a] - 0.1, 2), round(w[b] + 0.1, 2)
            out.append((f"Technical weights: {b} up 0.1, {a} down 0.1", c))
    for d in (-0.1, -0.05, 0.05, 0.1):
        t = round(params["threshold"] + d, 2)
        if 0.3 <= t <= 0.8:
            c = copy.deepcopy(params)
            c["threshold"] = t
            out.append((f"Entry threshold {params['threshold']} to {t}", c))
    for key, lo, hi, label in (("stop_atr", 1.0, 3.5, "Stop-loss distance"), ("target_atr", 1.5, 5.0, "Profit target distance")):
        for d in (-0.5, 0.5):
            v = round(params[key] + d, 2)
            c = copy.deepcopy(params)
            c[key] = v
            if lo <= v <= hi and c["target_atr"] >= c["stop_atr"]:
                out.append((f"{label} {params[key]} to {v} x ATR", c))
    return out


# --------------------------------------------------------------------------- bookkeeping
def _last_promotion() -> datetime | None:
    with db.connect() as conn:
        r = conn.execute("SELECT created FROM model_log WHERE decision='promoted' ORDER BY id DESC LIMIT 1").fetchone()
    return datetime.fromisoformat(r["created"]) if r else None


def _cooldown_fail(gate: Gate, now: datetime) -> str | None:
    last = _last_promotion()
    if last and now - last < timedelta(days=gate.cooldown_days):
        return f"cooldown: last change was {(now - last).days} days ago, minimum {gate.cooldown_days}"
    return None


def _parent(version: str) -> str | None:
    with db.connect() as conn:
        r = conn.execute("SELECT backtest FROM model_versions WHERE version=?", (version,)).fetchone()
    return (json.loads(r["backtest"]) if r and r["backtest"] else {}).get("previous_version")


# --------------------------------------------------------------------------- technical tuning
def tune_technical(prepared: dict, split: str, version: str, params: dict, gate: Gate, now: datetime) -> dict:
    a_ins, a_oos = evaluate(prepared, params, split)
    best = None
    for desc, cand in candidates(params):
        ins, oos = evaluate(prepared, cand, split)   # only the in-sample half is used to rank candidates
        if len(ins) < gate.min_is_trades:
            continue
        m = float(np.mean([t["pnl"] for t in ins]))
        if best is None or m > best[0]:
            best = (m, desc, cand, ins, oos)
    a_is_mean = _mean_std([t["pnl"] for t in a_ins])[1]
    if best is None or best[0] <= a_is_mean:
        model.log("technical", "Search around the active settings", "rejected",
                  "No candidate beat the active model on the in-sample period", {"candidates": len(candidates(params))})
        return {"decision": "rejected"}

    _, desc, cand, c_ins, c_oos = best
    c_pnl, a_pnl = [t["pnl"] for t in c_oos], [t["pnl"] for t in a_oos]
    _, c_m, _ = _mean_std(c_pnl)
    _, a_m, _ = _mean_std(a_pnl)
    z = z_score(c_pnl, a_pnl)
    c_dd = backtest.metrics(c_oos).get("approx_max_drawdown_pct", 0.0)
    a_dd = backtest.metrics(a_oos).get("approx_max_drawdown_pct", 0.0)

    fails = []
    if len(c_oos) < gate.min_oos_trades:
        fails.append(f"only {len(c_oos)} out-of-sample trades, need {gate.min_oos_trades}")
    if gate.require_positive and c_m <= 0:
        fails.append(f"out-of-sample average trade {c_m:+.2f}% is not positive after costs")
    if c_m - a_m < gate.min_improvement:
        fails.append(f"out-of-sample gain {c_m - a_m:+.2f}% is below the {gate.min_improvement:.2f}% minimum")
    if z < gate.z_min:
        fails.append(f"z {z:.1f} is below {gate.z_min} (could be luck)")
    if a_dd > 0 and c_dd > a_dd * gate.max_dd_ratio:
        fails.append(f"drawdown {c_dd:.1f}% is worse than {a_dd:.1f}%")
    cd = _cooldown_fail(gate, now)
    if cd:
        fails.append(cd)

    detail = {"candidate_oos_avg": c_m, "active_oos_avg": a_m, "z": z, "candidate_oos_trades": len(c_oos),
              "candidate_is_avg": best[0], "active_is_avg": a_is_mean, "split_date": split}
    if fails:
        model.log("technical", desc, "rejected", "; ".join(fails), detail)
        return {"decision": "rejected", "change": desc, "reasons": fails, **detail}

    bt = {"in_sample": backtest.metrics(c_ins), "out_of_sample": backtest.metrics(c_oos), "split_date": split,
          "previous_version": version, "previous_out_of_sample": backtest.metrics(a_oos), "z": z}
    reason = f"Out-of-sample average trade {c_m:+.2f}% vs {a_m:+.2f}% for {version} (z {z:.1f}), {len(c_oos)} trades"
    name = model.save_version(cand, bt, f"{desc}. {reason}")
    model.log("technical", desc, "promoted", f"{reason}; now {name}", detail)
    return {"decision": "promoted", "version": name, "change": desc, **detail}


# --------------------------------------------------------------------------- blend tuning (from graded live calls)
def _graded() -> list:
    with db.connect() as conn:
        return conn.execute("SELECT id, action, pnl_pct, score_tech, score_fund, score_sent FROM recommendations "
                            "WHERE status IN ('WIN','LOSS') AND pnl_pct IS NOT NULL ORDER BY COALESCE(closed,''), id").fetchall()


def replay(rows: list, blend: dict, threshold: float) -> list[float]:
    """Returns of the calls a blend would have taken, signed by the direction it would have chosen. Only calls the
    live model actually made can be replayed, so this can re-weight or filter them but cannot discover new ones."""
    out = []
    for r in rows:
        comps = [(r["score_tech"] or 0.0, blend["technical"])]
        if abs(r["score_fund"] or 0.0) > 1e-9:    # exactly 0 means "no data" and is left out, as in the live engine
            comps.append((r["score_fund"], blend["fundamental"]))
        if abs(r["score_sent"] or 0.0) > 1e-9:
            comps.append((r["score_sent"], blend["sentiment"]))
        w = sum(x for _, x in comps)
        c = sum(v * x for v, x in comps) / w if w else 0.0
        if abs(c) < threshold:
            continue
        buy_return = r["pnl_pct"] if r["action"] == "BUY" else -r["pnl_pct"]
        out.append((1 if c > 0 else -1) * buy_return)
    return out


def blend_grid() -> list[dict]:
    g = []
    for wt in range(3, 8):
        for wf in range(1, 6):
            ws = 10 - wt - wf
            if 0 <= ws <= 3:
                g.append({"technical": wt / 10, "fundamental": wf / 10, "sentiment": ws / 10})
    return g


def tune_blend(version: str, params: dict, gate: Gate, now: datetime) -> dict:
    rows = _graded()
    if len(rows) < gate.min_graded:
        model.log("blend", "Re-weight technical / fundamental / news scores", "skipped",
                  f"Only {len(rows)} graded live calls so far; {gate.min_graded} are needed", {"graded": len(rows)})
        return {"decision": "skipped"}
    cut = int(len(rows) * 0.7)
    fit, ev = rows[:cut], rows[cut:]
    th = params["threshold"]
    best = None
    for b in blend_grid():
        r = replay(fit, b, th)
        if len(r) >= gate.blend_fit_min:
            m = float(np.mean(r))
            if best is None or m > best[0]:
                best = (m, b)
    if best is None or best[1] == params["blend"]:
        model.log("blend", "Re-weight technical / fundamental / news scores", "rejected", "No better blend found on the older calls")
        return {"decision": "rejected"}

    cand = best[1]
    c_ev, a_ev = replay(ev, cand, th), replay(ev, params["blend"], th)
    _, c_m, _ = _mean_std(c_ev)
    _, a_m, _ = _mean_std(a_ev)
    z = z_score(c_ev, a_ev)
    desc = "Blend weights technical/fundamental/news " + "/".join(f"{int(cand[k] * 100)}" for k in ("technical", "fundamental", "sentiment"))
    fails = []
    if len(c_ev) < gate.blend_min_taken or len(a_ev) < gate.blend_min_taken:
        fails.append(f"only {len(c_ev)} newer calls to judge on, need {gate.blend_min_taken}")
    if gate.require_positive and c_m <= 0:
        fails.append(f"average return {c_m:+.2f}% on newer calls is not positive")
    if c_m - a_m < gate.blend_min_improvement:
        fails.append(f"gain {c_m - a_m:+.2f}% is below the {gate.blend_min_improvement:.2f}% minimum")
    if z < gate.z_min:
        fails.append(f"z {z:.1f} is below {gate.z_min}")
    cd = _cooldown_fail(gate, now)
    if cd:
        fails.append(cd)
    detail = {"candidate_avg": c_m, "active_avg": a_m, "z": z, "judged_on": len(c_ev), "fit_calls": len(fit), "eval_calls": len(ev)}
    if fails:
        model.log("blend", desc, "rejected", "; ".join(fails), detail)
        return {"decision": "rejected", "reasons": fails, **detail}

    new = copy.deepcopy(params)
    new["blend"] = cand
    reason = f"Replayed on {len(ev)} newer graded calls: {c_m:+.2f}% vs {a_m:+.2f}% (z {z:.1f})"
    name = model.save_version(new, {"previous_version": version, "live_replay": detail}, f"{desc}. {reason}")
    model.log("blend", desc, "promoted", f"{reason}; now {name}", detail)
    return {"decision": "promoted", "version": name, **detail}


# --------------------------------------------------------------------------- rollback
def rollback(reason: str = "Manual rollback") -> str | None:
    db.init_db()
    version, _ = model.active()
    parent = _parent(version)
    if not parent:
        model.log("rollback", f"Roll back {version}", "skipped", "This version has no recorded parent to return to")
        return None
    with db.connect() as conn:
        conn.execute("UPDATE model_versions SET active=0")
        conn.execute("UPDATE model_versions SET active=1 WHERE version=?", (parent,))
    model.log("rollback", f"{version} back to {parent}", "rollback", reason)
    log.warning("Rolled back %s -> %s: %s", version, parent, reason)
    return parent


def check_live(gate: Gate) -> str | None:
    """Compare the active version's graded live calls with its parent's. Roll back if it is losing money AND doing
    worse than the version it replaced."""
    version, _ = model.active()
    parent = _parent(version)
    if not parent:
        return None

    def stat(v: str) -> tuple[int, float]:
        with db.connect() as conn:
            rows = conn.execute("SELECT pnl_pct FROM recommendations WHERE model_version=? AND status IN ('WIN','LOSS') AND pnl_pct IS NOT NULL", (v,)).fetchall()
        return len(rows), (float(np.mean([r["pnl_pct"] for r in rows])) if rows else 0.0)

    n, m = stat(version)
    pn, pm = stat(parent)
    if n >= gate.live_check_min and pn >= gate.live_check_min and m < 0 and pm > m:
        return rollback(f"Live record of {version} ({m:+.2f}% average over {n} calls) is worse than {parent} ({pm:+.2f}% over {pn})")
    return None


# --------------------------------------------------------------------------- one cycle
def run(frames: dict | None = None, gate: Gate | None = None, now: datetime | None = None) -> dict:
    db.init_db()
    gate, now = gate or Gate(), now or datetime.now(timezone.utc)
    result: dict = {}
    result["rolled_back_to"] = check_live(gate)
    version, params = model.active()

    if frames is None:
        frames = {s: price_data.get_prices(s) for s in universe.active_symbols()}
    prepared = prepare(frames)
    if not prepared:
        model.log("technical", "Search around the active settings", "skipped", "Not enough price history (need 260+ daily bars per ticker)")
        result["technical"] = {"decision": "skipped"}
    else:
        split = split_date(prepared)
        _, a_oos = evaluate(prepared, params, split)
        _, a_m, _ = _mean_std([t["pnl"] for t in a_oos])
        if len(a_oos) >= gate.min_oos_trades and a_m <= 0:
            model.log("health", f"Check of {version}", "warning",
                      f"Out-of-sample average trade is {a_m:+.2f}% after costs over {len(a_oos)} trades. The active model shows no "
                      "edge on recent history; do not trade real money on its signals.", {"avg": a_m, "trades": len(a_oos)})
        result["technical"] = tune_technical(prepared, split, version, params, gate, now)

    version, params = model.active()   # a promotion above changes the active model
    result["blend"] = tune_blend(version, params, gate, now)
    model.log("cycle", "Learning cycle", "done", "", {"active": model.active()[0]})
    log.info("Learning cycle finished: %s", {k: (v.get("decision") if isinstance(v, dict) else v) for k, v in result.items()})
    return result


def maybe_run(min_days: int = 7, gate: Gate | None = None, now: datetime | None = None) -> dict | None:
    """Run a cycle only if the last one was at least `min_days` ago (called from run_daily)."""
    db.init_db()
    now = now or datetime.now(timezone.utc)
    with db.connect() as conn:
        r = conn.execute("SELECT created FROM model_log WHERE kind='cycle' ORDER BY id DESC LIMIT 1").fetchone()
    if r and now - datetime.fromisoformat(r["created"]) < timedelta(days=min_days):
        return None
    return run(gate=gate, now=now)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", nargs="?", default="run", choices=["run", "rollback"])
    a = ap.parse_args()
    if a.cmd == "rollback":
        print("Active model is now", rollback() or "unchanged")
    else:
        print(json.dumps(run(), indent=2, default=str))
