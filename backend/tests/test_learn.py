"""Self-improvement loop: candidate generation, promotion gate, blend tuning, cooldown, rollback, scheduling."""
import json
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from app import config, db, engine, learn, model, recs_store
from app.learn import Gate

LENIENT = Gate(min_is_trades=1, min_oos_trades=1, min_improvement=-99, z_min=-99, require_positive=False,
               max_dd_ratio=99, cooldown_days=0, min_graded=10, blend_fit_min=3, blend_min_taken=3, blend_min_improvement=-99)


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "l.db"))
    db.init_db()


def random_walks(k=8, n=900, seed0=0, vol=0.012):
    out = {}
    for s in range(k):
        r = np.random.default_rng(seed0 + s)
        idx = pd.bdate_range(end="2026-09-30", periods=n)
        c = 100 * np.exp(np.cumsum(r.normal(0, vol, n)))
        o = np.r_[c[0], c[:-1]]
        out[f"R{s}"] = pd.DataFrame({"open": o, "high": np.maximum(o, c) * 1.004, "low": np.minimum(o, c) * 0.996, "close": c, "volume": 1e6},
                                    index=idx)
    return out


def log_rows(decision=None):
    with db.connect() as conn:
        q = "SELECT * FROM model_log WHERE kind != 'cycle'" + (" AND decision=?" if decision else "")
        return conn.execute(q, (decision,) if decision else ()).fetchall()


# ---- pure pieces ----------------------------------------------------------------
def test_candidates_are_valid_single_steps():
    p = model.DEFAULT_PARAMS
    cands = learn.candidates(p)
    assert 10 <= len(cands) <= 20
    for desc, c in cands:
        assert abs(sum(c["tech"].values()) - 1.0) < 1e-9 and min(c["tech"].values()) >= 0.05 - 1e-9
        assert 0.3 <= c["threshold"] <= 0.8 and c["target_atr"] >= c["stop_atr"]
        changed = [k for k in ("tech", "threshold", "stop_atr", "target_atr") if c[k] != p[k]]
        assert len(changed) == 1, desc                     # exactly one kind of change per candidate


def test_z_score():
    rng = np.random.default_rng(0)
    better, base = list(rng.normal(0.5, 1, 400)), list(rng.normal(0.0, 1, 400))
    assert learn.z_score(better, base) > 4
    assert abs(learn.z_score(list(rng.normal(0, 1, 400)), base)) < 3
    assert learn.z_score([1.0], [2.0]) == 0.0              # too few trades: no evidence


def test_normalise_upgrades_legacy_weights():
    p = model.normalise({"technical": 0.6, "fundamental": 0.3, "sentiment": 0.1})
    assert p["blend"]["technical"] == 0.6 and p["stop_atr"] == 2.0 and "trend" in p["tech"]


# ---- technical tuning --------------------------------------------------------------
def test_strict_gate_rejects_on_pure_noise_and_flags_no_edge():
    res = learn.run(frames=random_walks(), gate=Gate(min_is_trades=50, min_oos_trades=50))
    assert res["technical"]["decision"] == "rejected"
    assert model.active()[0] == "v1"                       # nothing promoted
    assert not log_rows("promoted")
    assert log_rows("rejected")
    warn = log_rows("warning")                             # active model has no edge on random data: say so
    assert warn and "do not trade real money" in warn[0]["reason"]


def test_promotion_creates_version_and_engine_uses_it():
    res = learn.run(frames=random_walks(), gate=LENIENT)
    assert res["technical"]["decision"] == "promoted"
    version, params = model.active()
    assert version == "v2" and params != model.DEFAULT_PARAMS
    perf = recs_store.performance()
    assert [v["version"] for v in perf["versions"]] == ["v1", "v2"]
    assert any(l["decision"] == "promoted" for l in perf["log"]) and not any(l["kind"] == "cycle" for l in perf["log"])
    with db.connect() as conn:
        row = conn.execute("SELECT backtest FROM model_versions WHERE version='v2'").fetchone()
    assert json.loads(row["backtest"])["previous_version"] == "v1"
    # engine reads the active version's settings
    assert engine.levels("BUY", 100, 2, params["stop_atr"], params["target_atr"])[1] == pytest.approx(100 - params["stop_atr"] * 2)


def test_cooldown_blocks_second_change():
    learn.run(frames=random_walks(), gate=LENIENT)
    g = Gate(**{**LENIENT.__dict__, "cooldown_days": 30})
    res = learn.run(frames=random_walks(), gate=g)
    assert res["technical"]["decision"] == "rejected"
    assert any("cooldown" in r["reason"] for r in log_rows("rejected"))
    assert model.active()[0] == "v2"


def test_rollback_restores_parent():
    learn.run(frames=random_walks(), gate=LENIENT)
    assert model.active()[0] == "v2"
    assert learn.rollback("test") == "v1"
    assert model.active() == ("v1", model.normalise(model.DEFAULT_PARAMS))
    assert learn.rollback("again") is None                 # v1 has no parent
    assert log_rows("rollback")


def test_insufficient_history_is_skipped():
    res = learn.run(frames={"X": random_walks(1, 120)["R0"]}, gate=Gate())
    assert res["technical"]["decision"] == "skipped"


# ---- blend tuning from graded live calls -----------------------------------------
def add_graded(n, fund_predictive=True, version="v1", seed=0):
    r = np.random.default_rng(seed)
    with db.connect() as conn:
        for i in range(n):
            fund = float(r.choice([-0.8, -0.4, 0.4, 0.8]))
            tech = float(r.choice([-0.8, 0.8]))
            sent = float(r.uniform(-0.5, 0.5))
            pnl = (fund * 4 if fund_predictive else tech * 2) + r.normal(0, 1)
            action = "BUY"
            conn.execute("INSERT INTO recommendations(batch,created,symbol,action,entry,target,stop,size_pct,strength,combined,"
                         "score_tech,score_fund,score_sent,model_version,status,pnl_pct,closed) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                         ("b", "2026-08-01T00:00:00+00:00", f"S{i}", action, 100, 109, 94, 3, 0.7, 0.7, tech, fund, sent, version,
                          "WIN" if pnl > 0 else "LOSS", pnl, f"2026-09-{1 + i % 28:02d}"))


def test_blend_skipped_without_enough_graded_calls():
    add_graded(20)
    v, p = model.active()
    assert learn.tune_blend(v, p, Gate(), datetime.now(timezone.utc))["decision"] == "skipped"


def test_blend_shifts_weight_to_the_predictive_component():
    add_graded(200, fund_predictive=True)
    v, p = model.active()
    g = Gate(blend_min_improvement=0.0, z_min=0.0, cooldown_days=0, blend_min_taken=10, min_graded=60)
    p = model.normalise(p); p["threshold"] = 0.2
    out = learn.tune_blend(v, p, g, datetime.now(timezone.utc))
    assert out["decision"] == "promoted"
    new = model.active()[1]["blend"]
    assert new["fundamental"] > p["blend"]["fundamental"] and abs(sum(new.values()) - 1.0) < 1e-9


# ---- live check + scheduling -------------------------------------------------------
def test_live_check_rolls_back_a_losing_version():
    learn.run(frames=random_walks(), gate=LENIENT)         # -> v2 (parent v1)
    add_graded(45, version="v1", seed=1)                   # parent has a good record
    with db.connect() as conn:
        conn.execute("UPDATE recommendations SET pnl_pct = ABS(pnl_pct) + 0.5 WHERE model_version='v1'")
    add_graded(45, version="v2", seed=2)
    with db.connect() as conn:
        conn.execute("UPDATE recommendations SET pnl_pct = -(ABS(pnl_pct) + 0.5) WHERE model_version='v2'")
    assert learn.check_live(Gate()) == "v1" and model.active()[0] == "v1"


def test_maybe_run_respects_weekly_cadence():
    frames = {"X": random_walks(1, 120)["R0"]}
    now = datetime.now(timezone.utc)
    assert learn.maybe_run(now=now, gate=Gate()) is not None   # first run happens
    assert learn.maybe_run(now=now + timedelta(days=3), gate=Gate()) is None
    assert learn.maybe_run(now=now + timedelta(days=8), gate=Gate()) is not None
