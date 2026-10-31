"""Data pipeline runner.  Usage (from backend/):

    python -m app.pipeline            # full run: universe, prices, fundamentals, news
    python -m app.pipeline --only prices news
    python -m app.pipeline --limit 20 # first 20 tickers only (quick test)

Schedule it (cron / systemd / GitHub Actions) twice a day; step 3 will hook the recommendation engine after it.
"""
from __future__ import annotations

import argparse
import logging
from datetime import datetime, timezone

from . import config, db
from .data import fundamentals, news, prices, universe

log = logging.getLogger("pipeline")
STEPS = ("universe", "prices", "fundamentals", "news")


def _record(step: str, ok: bool, detail: str, started: str) -> None:
    with db.connect() as conn:
        conn.execute("INSERT INTO pipeline_runs(started,finished,step,ok,detail) VALUES(?,?,?,?,?)",
                     (started, datetime.now(timezone.utc).isoformat(), step, int(ok), detail))


def run(only: list[str] | None = None, limit: int | None = None) -> None:
    db.init_db()
    wanted = only or list(STEPS)
    for step in STEPS:
        if step not in wanted:
            continue
        started = datetime.now(timezone.utc).isoformat()
        try:
            if step == "universe":
                detail = f"{universe.load_universe(config.UNIVERSE)} tickers loaded"
            else:
                syms = universe.active_symbols()[:limit]
                fn = {"prices": prices.update_prices, "fundamentals": fundamentals.update_fundamentals,
                      "news": news.update_news}[step]
                res = fn(syms)
                detail = f"{sum(1 for v in res.values() if v)}/{len(syms)} tickers updated, {sum(res.values())} rows"
            log.info("%s: %s", step, detail)
            _record(step, True, detail, started)
        except Exception as e:  # keep going so one failing source does not block the others
            log.exception("%s failed", step)
            _record(step, False, str(e), started)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="+", choices=STEPS)
    ap.add_argument("--limit", type=int)
    a = ap.parse_args()
    run(a.only, a.limit)
