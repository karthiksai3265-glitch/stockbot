"""Model parameters and versions.

A model version is a full set of settings stored as JSON in model_versions.weights:

    blend       how technical / fundamental / news scores are combined (live engine)
    tech        weights inside the technical score: trend, momentum, rsi
    threshold   minimum signal strength to recommend (also the backtest entry threshold)
    stop_atr    stop-loss distance in multiples of ATR
    target_atr  profit-target distance in multiples of ATR

Older rows that stored only the blend weights are upgraded on read.
"""
from __future__ import annotations

import copy
import json
from datetime import datetime, timezone

from . import db
from .analysis import signals, technical

DEFAULT_PARAMS = {
    "blend": dict(signals.DEFAULT_WEIGHTS),
    "tech": dict(technical.DEFAULT_TECH_WEIGHTS),
    "threshold": 0.5,
    "stop_atr": 2.0,
    "target_atr": 3.0,
}
BASELINE_NOTE = "Baseline: trend + momentum + RSI technical score, fundamentals and news blended in"


def normalise(raw: dict | None) -> dict:
    p = copy.deepcopy(DEFAULT_PARAMS)
    if not raw:
        return p
    if "blend" in raw:
        p["blend"].update(raw["blend"])
    elif {"technical", "fundamental", "sentiment"} <= raw.keys():   # legacy: blend weights only
        p["blend"] = {k: raw[k] for k in ("technical", "fundamental", "sentiment")}
    if "tech" in raw:
        p["tech"].update(raw["tech"])
    for k in ("threshold", "stop_atr", "target_atr"):
        if k in raw:
            p[k] = raw[k]
    return p


def ensure_baseline() -> None:
    with db.connect() as conn:
        if conn.execute("SELECT 1 FROM model_versions LIMIT 1").fetchone():
            return
        conn.execute("INSERT INTO model_versions(version,created,weights,note,active) VALUES('v1',?,?,?,1)",
                     (datetime.now(timezone.utc).isoformat(), json.dumps(DEFAULT_PARAMS), BASELINE_NOTE))


def active() -> tuple[str, dict]:
    """(version, params) of the model currently used for live recommendations."""
    db.init_db()
    ensure_baseline()
    with db.connect() as conn:
        row = conn.execute("SELECT version, weights FROM model_versions WHERE active=1 ORDER BY created DESC, rowid DESC LIMIT 1").fetchone()
    return row["version"], normalise(json.loads(row["weights"]))


def next_version_name() -> str:
    with db.connect() as conn:
        n = len(conn.execute("SELECT version FROM model_versions").fetchall())
    return f"v{n + 1}"


def save_version(params: dict, backtest: dict | None, note: str, activate: bool = True) -> str:
    name = next_version_name()
    with db.connect() as conn:
        if activate:
            conn.execute("UPDATE model_versions SET active=0")
        conn.execute("INSERT INTO model_versions(version,created,weights,backtest,note,active) VALUES(?,?,?,?,?,?)",
                     (name, datetime.now(timezone.utc).isoformat(), json.dumps(params),
                      json.dumps(backtest) if backtest else None, note, int(activate)))
    return name


def log(kind: str, change: str, decision: str, reason: str, detail: dict | None = None) -> None:
    with db.connect() as conn:
        conn.execute("INSERT INTO model_log(created,kind,change,decision,reason,detail) VALUES(?,?,?,?,?,?)",
                     (datetime.now(timezone.utc).isoformat(), kind, change, decision, reason, json.dumps(detail or {})))
