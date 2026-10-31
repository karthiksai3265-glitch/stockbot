"""One full update: refresh data -> grade past calls -> generate recommendations -> update paper portfolio
-> order tickets (only if a broker is configured; you still approve each one) -> (weekly) self-improvement cycle.

    python -m app.run_daily          # run it once by hand
    python -m app.serve --schedule   # or let the website run it for you at STOCKBOT_RUN_TIMES (default 07:30 and 17:30)

If you prefer cron instead of --schedule:
    30 7  * * 1-5  cd /path/to/backend && python -m app.run_daily   # before ASX open
    30 17 * * 1-5  cd /path/to/backend && python -m app.run_daily   # after ASX close / before US open
"""
import logging

from . import engine, execution, grading, learn, pipeline, portfolio

log = logging.getLogger("run_daily")


def run_once() -> dict:
    """Runs every step. A failing optional step is logged and does not stop the others."""
    out: dict = {}
    pipeline.run()
    grading.grade_open()
    out["recommendations"] = len(engine.generate())
    out["portfolio"] = portfolio.sync()
    try:
        out["tickets"] = execution.propose()   # creates PENDING order tickets only when a broker is set; never sends orders
    except Exception:
        log.exception("Creating order tickets failed")
    try:
        out["learning"] = learn.maybe_run()    # self-improvement cycle, at most once every 7 days
    except Exception:                          # learning must never break the daily run
        log.exception("Learning cycle failed")
    return out


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    run_once()
