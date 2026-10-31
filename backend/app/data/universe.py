"""Stock universe loaders (S&P 500 and ASX 200).

S&P 500 constituents come from the Wikipedia table; ASX 200 from the official ASX/S&P
index list when reachable, with a small built-in fallback so the app still works offline.
ASX tickers use the ".AX" suffix for Yahoo Finance.
"""
from __future__ import annotations

import io
import logging

import pandas as pd
import requests

from .. import db

log = logging.getLogger(__name__)
UA = {"User-Agent": "Mozilla/5.0 (stockbot research tool)"}

# Fallback so first run works without network access to the list sources.
FALLBACK = {
    "SP500": [
        ("AAPL", "Apple", "Information Technology"), ("MSFT", "Microsoft", "Information Technology"),
        ("NVDA", "NVIDIA", "Information Technology"), ("AMZN", "Amazon", "Consumer Discretionary"),
        ("GOOGL", "Alphabet", "Communication Services"), ("JPM", "JPMorgan Chase", "Financials"),
        ("XOM", "Exxon Mobil", "Energy"), ("JNJ", "Johnson & Johnson", "Health Care"),
    ],
    "ASX200": [
        ("BHP.AX", "BHP Group", "Materials"), ("CBA.AX", "Commonwealth Bank", "Financials"),
        ("CSL.AX", "CSL", "Health Care"), ("WBC.AX", "Westpac", "Financials"),
        ("NAB.AX", "National Australia Bank", "Financials"), ("WES.AX", "Wesfarmers", "Consumer Staples"),
        ("FMG.AX", "Fortescue", "Materials"), ("TLS.AX", "Telstra", "Communication Services"),
    ],
    "NIFTY50": [
        ("RELIANCE.NS", "Reliance Industries", "Energy"), ("TCS.NS", "Tata Consultancy Services", "Information Technology"),
        ("HDFCBANK.NS", "HDFC Bank", "Financials"), ("INFY.NS", "Infosys", "Information Technology"),
        ("ICICIBANK.NS", "ICICI Bank", "Financials"), ("BHARTIARTL.NS", "Bharti Airtel", "Communication Services"),
        ("ITC.NS", "ITC", "Consumer Staples"), ("LT.NS", "Larsen & Toubro", "Industrials"),
    ],
}


def _sp500() -> list[tuple[str, str, str]]:
    r = requests.get("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies", headers=UA, timeout=20)
    r.raise_for_status()
    df = pd.read_html(io.StringIO(r.text))[0]
    return [(s.replace(".", "-"), n, sec) for s, n, sec in zip(df["Symbol"], df["Security"], df["GICS Sector"])]


def _asx200() -> list[tuple[str, str, str]]:
    r = requests.get("https://en.wikipedia.org/wiki/S%26P/ASX_200", headers=UA, timeout=20)
    r.raise_for_status()
    for t in pd.read_html(io.StringIO(r.text)):
        cols = {str(c).lower(): c for c in t.columns}
        code = next((cols[c] for c in cols if "code" in c or "ticker" in c), None)
        name = next((cols[c] for c in cols if "company" in c or "name" in c), None)
        if code is not None and len(t) > 100:
            sector = next((cols[c] for c in cols if "sector" in c), None)
            return [(f"{str(c).strip()}.AX", str(n), str(s) if sector is not None else "")
                    for c, n, s in zip(t[code], t[name], t[sector] if sector is not None else [""] * len(t))]
    raise ValueError("ASX 200 table not found")


def _nifty50() -> list[tuple[str, str, str]]:
    """Nifty 50 (NSE India) constituents from Wikipedia. NSE tickers use the ".NS" suffix on Yahoo Finance."""
    r = requests.get("https://en.wikipedia.org/wiki/NIFTY_50", headers=UA, timeout=20)
    r.raise_for_status()
    for t in pd.read_html(io.StringIO(r.text)):
        cols = {str(c).lower(): c for c in t.columns}
        sym = next((cols[c] for c in cols if "symbol" in c), None)
        name = next((cols[c] for c in cols if "company" in c or "name" in c), None)
        if sym is not None and len(t) >= 40:
            sector = next((cols[c] for c in cols if "sector" in c or "industry" in c), None)
            return [(f"{str(s).strip()}.NS", str(n), str(sec) if sector is not None else "")
                    for s, n, sec in zip(t[sym], t[name] if name is not None else t[sym], t[sector] if sector is not None else [""] * len(t))]
    raise ValueError("Nifty 50 table not found")


LOADERS = {"SP500": _sp500, "ASX200": _asx200, "NIFTY50": _nifty50}
CURRENCY = {"SP500": "USD", "ASX200": "AUD", "NIFTY50": "INR"}


def load_universe(markets: list[str]) -> int:
    """Fetch constituents for each market and upsert into the tickers table."""
    total = 0
    with db.connect() as conn:
        for market in markets:
            try:
                rows = LOADERS[market]()
            except Exception as e:  # network / parsing problems -> fallback list
                log.warning("Could not load %s (%s); using built-in fallback list", market, e)
                rows = FALLBACK[market]
            for sym, name, sector in rows:
                conn.execute(
                    "INSERT INTO tickers(symbol,name,market,sector,currency) VALUES(?,?,?,?,?) "
                    "ON CONFLICT(symbol) DO UPDATE SET name=excluded.name, sector=excluded.sector, active=1",
                    (sym, name, market, sector, CURRENCY[market]),
                )
            total += len(rows)
    return total


def active_symbols() -> list[str]:
    with db.connect() as conn:
        return [r["symbol"] for r in conn.execute("SELECT symbol FROM tickers WHERE active=1 ORDER BY symbol")]
