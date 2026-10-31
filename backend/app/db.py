"""SQLite database layer. Plain sqlite3 so there are no extra dependencies.

Tables
------
tickers        the stock universe
prices         daily OHLCV bars
fundamentals   quarterly financial statement metrics (sales, earnings, debt, ...)
news           headlines per ticker with sentiment score
recommendations (created in step 3) log of every recommendation, graded later
"""
import sqlite3
from contextlib import contextmanager

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS tickers (
    symbol      TEXT PRIMARY KEY,
    name        TEXT,
    market      TEXT NOT NULL,          -- SP500 / ASX200
    sector      TEXT,
    currency    TEXT,
    active      INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS prices (
    symbol  TEXT NOT NULL,
    date    TEXT NOT NULL,              -- YYYY-MM-DD
    open    REAL, high REAL, low REAL, close REAL, adj_close REAL,
    volume  REAL,
    PRIMARY KEY (symbol, date)
);

CREATE TABLE IF NOT EXISTS fundamentals (
    symbol          TEXT NOT NULL,
    period_end      TEXT NOT NULL,      -- quarter end date
    revenue         REAL,               -- sales
    gross_profit    REAL,
    operating_income REAL,
    net_income      REAL,
    total_debt      REAL,
    cash            REAL,
    operating_cash_flow REAL,
    free_cash_flow  REAL,
    shares_outstanding REAL,
    PRIMARY KEY (symbol, period_end)
);

CREATE TABLE IF NOT EXISTS news (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol      TEXT,                   -- NULL = general market news
    published   TEXT,
    title       TEXT NOT NULL,
    publisher   TEXT,
    url         TEXT,
    sentiment   REAL,                   -- -1 (very negative) .. +1 (very positive)
    sentiment_source TEXT,              -- 'llm' or 'keyword'
    UNIQUE (symbol, url)
);

CREATE INDEX IF NOT EXISTS idx_prices_symbol_date ON prices(symbol, date);
CREATE INDEX IF NOT EXISTS idx_news_symbol ON news(symbol, published);

CREATE TABLE IF NOT EXISTS pipeline_runs (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    started   TEXT NOT NULL,
    finished  TEXT,
    step      TEXT NOT NULL,
    ok        INTEGER,
    detail    TEXT
);

CREATE TABLE IF NOT EXISTS model_versions (
    version   TEXT PRIMARY KEY,
    created   TEXT NOT NULL,
    weights   TEXT NOT NULL,            -- JSON {"technical":..,"fundamental":..,"sentiment":..}
    backtest  TEXT,                     -- JSON backtest summary
    note      TEXT,
    active    INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS meta (
    key    TEXT PRIMARY KEY,
    value  TEXT
);

CREATE TABLE IF NOT EXISTS order_tickets (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    rec_id          INTEGER,
    symbol          TEXT NOT NULL,
    side            TEXT NOT NULL,              -- buy / sell
    qty             INTEGER NOT NULL,
    limit_price     REAL,
    stop            REAL,
    target          REAL,
    notional        REAL NOT NULL,
    status          TEXT NOT NULL DEFAULT 'PENDING',   -- PENDING / SUBMITTED / REJECTED / FAILED / EXPIRED / CANCELLED
    broker          TEXT,
    mode            TEXT,                       -- paper / live
    broker_order_id TEXT,
    message         TEXT,
    created         TEXT NOT NULL,
    decided         TEXT,
    UNIQUE (rec_id, side)
);

CREATE TABLE IF NOT EXISTS exec_state (
    id      INTEGER PRIMARY KEY CHECK (id = 1),
    halted  INTEGER NOT NULL DEFAULT 0          -- 1 = kill switch: no orders can be approved
);

CREATE TABLE IF NOT EXISTS model_log (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    created   TEXT NOT NULL,
    kind      TEXT NOT NULL,            -- technical / blend / rollback
    change    TEXT NOT NULL,            -- what was tried, in plain words
    decision  TEXT NOT NULL,            -- promoted / rejected / skipped
    reason    TEXT,
    detail    TEXT                      -- JSON numbers behind the decision
);

CREATE TABLE IF NOT EXISTS recommendations (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    batch       TEXT NOT NULL,          -- one run of the engine
    created     TEXT NOT NULL,
    symbol      TEXT NOT NULL,
    action      TEXT NOT NULL,          -- BUY / SELL
    entry       REAL NOT NULL,
    target      REAL NOT NULL,
    stop        REAL NOT NULL,
    size_pct    REAL NOT NULL,
    strength    REAL NOT NULL,          -- 0..1 signal strength (NOT a probability of winning)
    window      TEXT,
    reasons     TEXT,                   -- JSON list
    score_tech  REAL, score_fund REAL, score_sent REAL, combined REAL,
    model_version TEXT,
    status      TEXT NOT NULL DEFAULT 'OPEN',   -- OPEN / WIN / LOSS
    pnl_pct     REAL,                   -- after costs; unrealised while OPEN
    closed      TEXT
);
CREATE TABLE IF NOT EXISTS paper_state (
    id         INTEGER PRIMARY KEY CHECK (id = 1),
    cash       REAL NOT NULL,
    start_cash REAL NOT NULL,
    paused     INTEGER NOT NULL DEFAULT 0,     -- kill switch: 1 = no new entries
    created    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS paper_trades (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    rec_id      INTEGER UNIQUE,
    symbol      TEXT NOT NULL,
    sector      TEXT,
    notional    REAL NOT NULL,              -- amount invested, in the portfolio's base currency
    entry       REAL NOT NULL,
    stop        REAL NOT NULL,
    target      REAL NOT NULL,
    opened      TEXT NOT NULL,
    closed      TEXT,
    exit_price  REAL,
    exit_reason TEXT,                       -- stop / target / timeout / exit_signal
    pnl_pct     REAL,
    status      TEXT NOT NULL DEFAULT 'OPEN'   -- OPEN / CLOSED
);

CREATE TABLE IF NOT EXISTS paper_equity (
    date   TEXT PRIMARY KEY,
    value  REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_recs_batch ON recommendations(batch);
CREATE INDEX IF NOT EXISTS idx_recs_symbol ON recommendations(symbol, status);
"""


@contextmanager
def connect(path: str | None = None):
    conn = sqlite3.connect(path or config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db(path: str | None = None) -> None:
    with connect(path) as conn:
        conn.executescript(SCHEMA)


def get_meta(key: str, default: str | None = None) -> str | None:
    init_db()
    with connect() as conn:
        r = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return r["value"] if r else default


def set_meta(key: str, value: str) -> None:
    init_db()
    with connect() as conn:
        conn.execute("INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
