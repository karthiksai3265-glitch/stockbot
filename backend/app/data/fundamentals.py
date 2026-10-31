"""Quarterly financial statement ingestion (sales, earnings, margins inputs, debt, cash flow).

Source: yfinance quarterly statements. For production-grade data swap this module for SEC EDGAR
(US) / ASX announcements or a paid provider; the rest of the app only reads the `fundamentals` table.
"""
from __future__ import annotations

import logging

import pandas as pd

from .. import db

log = logging.getLogger(__name__)

# our column -> candidate row labels in the statements (Yahoo labels vary)
FIELDS = {
    "revenue": ("income", ["Total Revenue", "Operating Revenue"]),
    "gross_profit": ("income", ["Gross Profit"]),
    "operating_income": ("income", ["Operating Income"]),
    "net_income": ("income", ["Net Income", "Net Income Common Stockholders"]),
    "total_debt": ("balance", ["Total Debt"]),
    "cash": ("balance", ["Cash And Cash Equivalents", "Cash Cash Equivalents And Short Term Investments"]),
    "operating_cash_flow": ("cashflow", ["Operating Cash Flow", "Cash Flow From Continuing Operating Activities"]),
    "free_cash_flow": ("cashflow", ["Free Cash Flow"]),
    "shares_outstanding": ("balance", ["Ordinary Shares Number", "Share Issued"]),
}


def _pick(df: pd.DataFrame, labels: list[str], col) -> float | None:
    if df is None or df.empty:
        return None
    for lab in labels:
        if lab in df.index and col in df.columns:
            v = df.loc[lab, col]
            if pd.notna(v):
                return float(v)
    return None


def extract_rows(symbol: str, income: pd.DataFrame, balance: pd.DataFrame, cashflow: pd.DataFrame) -> list[tuple]:
    stmts = {"income": income, "balance": balance, "cashflow": cashflow}
    periods = set()
    for df in stmts.values():
        if df is not None and not df.empty:
            periods.update(df.columns)
    out = []
    for p in sorted(periods):
        vals = [_pick(stmts[src], labels, p) for src, labels in FIELDS.values()]
        out.append((symbol, pd.Timestamp(p).strftime("%Y-%m-%d"), *vals))
    return out


def update_fundamentals(symbols: list[str]) -> dict[str, int]:
    import yfinance as yf

    added: dict[str, int] = {}
    with db.connect() as conn:
        for sym in symbols:
            try:
                t = yf.Ticker(sym)
                rows = extract_rows(sym, t.quarterly_income_stmt, t.quarterly_balance_sheet, t.quarterly_cashflow)
                conn.executemany(
                    "INSERT OR REPLACE INTO fundamentals(symbol,period_end,revenue,gross_profit,operating_income,"
                    "net_income,total_debt,cash,operating_cash_flow,free_cash_flow,shares_outstanding) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?)", rows)
                added[sym] = len(rows)
            except Exception as e:
                log.warning("Fundamentals failed for %s: %s", sym, e)
                added[sym] = 0
    return added


def get_fundamentals(symbol: str) -> pd.DataFrame:
    with db.connect() as conn:
        return pd.read_sql_query("SELECT * FROM fundamentals WHERE symbol=? ORDER BY period_end",
                                 conn, params=(symbol,), parse_dates=["period_end"])
