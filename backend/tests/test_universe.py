"""Universe loading falls back to the built-in lists when the web sources fail (e.g. offline)."""
import pytest

from app import config, db
from app.data import universe


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "u.db"))
    db.init_db()


def test_all_markets_fall_back_and_tag_currency(monkeypatch):
    def boom():
        raise ConnectionError("offline")
    monkeypatch.setattr(universe, "LOADERS", {m: boom for m in ("SP500", "ASX200", "NIFTY50")})
    assert universe.load_universe(["SP500", "ASX200", "NIFTY50"]) > 20
    with db.connect() as conn:
        cur = {r["market"]: r["currency"] for r in conn.execute("SELECT DISTINCT market, currency FROM tickers")}
        ns = [r["symbol"] for r in conn.execute("SELECT symbol FROM tickers WHERE market='NIFTY50'")]
    assert cur == {"SP500": "USD", "ASX200": "AUD", "NIFTY50": "INR"}
    assert ns and all(s.endswith(".NS") for s in ns)
