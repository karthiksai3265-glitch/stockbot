"""Reads recommendations/performance from the database in the shape the website expects (no web framework needed)."""
from __future__ import annotations

import json
from collections import defaultdict

from . import db


def _to_rec(r, meta: dict) -> dict:
    name, market, sector = meta.get(r["symbol"], (r["symbol"], "SP500", ""))
    rec = {
        "id": r["id"], "created": r["created"], "symbol": r["symbol"], "name": name, "market": market, "sector": sector, "action": r["action"],
        "entry": r["entry"], "window": r["window"] or "", "target": r["target"], "stop": r["stop"], "sizePct": r["size_pct"],
        "confidence": r["strength"], "reasons": json.loads(r["reasons"] or "[]"),
        "scores": {"technical": r["score_tech"] or 0, "fundamental": r["score_fund"] or 0, "sentiment": r["score_sent"] or 0},
    }
    if r["pnl_pct"] is not None or r["status"] != "OPEN":
        rec["outcome"] = {"status": r["status"], "pnlPct": r["pnl_pct"] or 0.0}
    return rec


def _meta(conn) -> dict:
    return {t["symbol"]: (t["name"] or t["symbol"], t["market"], t["sector"] or "") for t in conn.execute("SELECT symbol,name,market,sector FROM tickers")}


def latest_batch() -> list[dict]:
    db.init_db()
    with db.connect() as conn:
        b = conn.execute("SELECT MAX(batch) b FROM recommendations").fetchone()["b"]
        if not b:
            return []
        meta = _meta(conn)
        rows = conn.execute("SELECT * FROM recommendations WHERE batch=? ORDER BY ABS(combined) DESC", (b,)).fetchall()
        return [_to_rec(r, meta) for r in rows]


def history(limit: int = 200) -> list[dict]:
    db.init_db()
    with db.connect() as conn:
        meta = _meta(conn)
        rows = conn.execute("SELECT * FROM recommendations ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [_to_rec(r, meta) for r in rows]


def performance() -> dict:
    db.init_db()
    with db.connect() as conn:
        versions = conn.execute("SELECT * FROM model_versions ORDER BY created").fetchall()
        graded = conn.execute("SELECT model_version, status, closed FROM recommendations WHERE status IN ('WIN','LOSS')").fetchall()
        logrows = conn.execute("SELECT created, kind, change, decision, reason FROM model_log WHERE kind != 'cycle' ORDER BY id DESC LIMIT 30").fetchall()
    live = defaultdict(lambda: [0, 0])
    monthly = defaultdict(lambda: [0, 0])
    for g in graded:
        live[g["model_version"]][0] += g["status"] == "WIN"
        live[g["model_version"]][1] += 1
        monthly[(g["closed"] or "")[:7]][0] += g["status"] == "WIN"
        monthly[(g["closed"] or "")[:7]][1] += 1
    out_versions = []
    for v in versions:
        bt = json.loads(v["backtest"]) if v["backtest"] else {}
        oos = bt.get("out_of_sample") or {}
        wins, total = live.get(v["version"], [0, 0])
        out_versions.append({
            "version": v["version"], "promoted": v["created"][:10],
            "winRate": wins / total if total else float(oos.get("win_rate", 0)),
            "backtestReturnPct": float(oos.get("approx_total_return_pct", 0)),
            "note": (v["note"] or "") + (f" (live calls graded: {total})" if total else " (win rate shown is from out-of-sample backtest)"),
        })
    accuracy = [{"month": m, "winRate": w / t, "n": t} for m, (w, t) in sorted(monthly.items()) if m and t]
    backtest = {}
    for v in reversed(versions):                    # newest version that has a backtest on record
        if v["backtest"]:
            bt = json.loads(v["backtest"])
            if bt.get("out_of_sample") or bt.get("in_sample"):
                backtest = {"version": v["version"], "split_date": bt.get("split_date"), "in_sample": bt.get("in_sample") or {},
                            "out_of_sample": bt.get("out_of_sample") or {}, "buy_and_hold_avg_return_pct": bt.get("buy_and_hold_avg_return_pct")}
                break
    learning_log = [{"date": r["created"][:10], "kind": r["kind"], "change": r["change"], "decision": r["decision"], "reason": r["reason"] or ""}
                    for r in logrows]
    active = next((v["version"] for v in versions if v["active"]), None)
    return {"versions": out_versions, "accuracy": accuracy, "log": learning_log, "backtest": backtest, "active": active}
