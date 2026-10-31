"""News ingestion (yfinance headlines per ticker) with sentiment scoring.

Scoring order of preference:
1. LLM (Claude) when ANTHROPIC_API_KEY is set: headlines are scored in one batched call per ticker.
2. Keyword fallback so the pipeline always runs offline / without a key.
"""
from __future__ import annotations

import json
import logging
import re

from .. import config, db

log = logging.getLogger(__name__)

POS = {"beat", "beats", "surge", "surges", "soar", "soars", "record", "growth", "upgrade", "upgrades",
       "profit", "profits", "rally", "gain", "gains", "strong", "raises", "boost", "outperform", "buyback"}
NEG = {"miss", "misses", "plunge", "plunges", "fall", "falls", "drop", "drops", "downgrade", "downgrades",
       "loss", "losses", "lawsuit", "probe", "recall", "weak", "cuts", "cut", "warning", "fraud", "layoffs",
       "bankruptcy", "slump", "underperform"}


def keyword_sentiment(title: str) -> float:
    words = re.findall(r"[a-z']+", title.lower())
    pos = sum(w in POS for w in words)
    neg = sum(w in NEG for w in words)
    if pos == neg:
        return 0.0
    return max(-1.0, min(1.0, (pos - neg) / max(1, pos + neg)))


def llm_sentiment(titles: list[str], symbol: str | None) -> list[float] | None:
    """Score each headline from -1..+1 for the stock using Claude. Returns None if unavailable."""
    if not config.ANTHROPIC_API_KEY or not titles:
        return None
    try:
        import anthropic

        client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
        subject = f"the stock {symbol}" if symbol else "the overall stock market"
        numbered = "\n".join(f"{i + 1}. {t}" for i, t in enumerate(titles))
        msg = client.messages.create(
            model=config.LLM_MODEL,
            max_tokens=600,
            messages=[{"role": "user", "content":
                       f"Rate how each headline is likely to affect {subject} on a scale from -1 (very negative) "
                       f"to +1 (very positive); 0 if irrelevant or neutral. Reply with ONLY a JSON array of "
                       f"{len(titles)} numbers, same order.\n\n{numbered}"}],
        )
        text = msg.content[0].text
        scores = json.loads(re.search(r"\[.*\]", text, re.S).group(0))
        if len(scores) != len(titles):
            return None
        return [max(-1.0, min(1.0, float(s))) for s in scores]
    except Exception as e:
        log.warning("LLM sentiment failed, using keywords: %s", e)
        return None


def score_headlines(titles: list[str], symbol: str | None) -> tuple[list[float], str]:
    scores = llm_sentiment(titles, symbol)
    if scores is not None:
        return scores, "llm"
    return [keyword_sentiment(t) for t in titles], "keyword"


def store_news(conn, symbol: str | None, items: list[dict]) -> int:
    """items: dicts with title, url, publisher, published. Dedupes on (symbol, url)."""
    existing = {r["url"] for r in conn.execute("SELECT url FROM news WHERE symbol IS ?", (symbol,))}
    fresh = [i for i in items if i.get("title") and i.get("url") not in existing]
    if not fresh:
        return 0
    scores, source = score_headlines([i["title"] for i in fresh], symbol)
    conn.executemany(
        "INSERT OR IGNORE INTO news(symbol,published,title,publisher,url,sentiment,sentiment_source) VALUES(?,?,?,?,?,?,?)",
        [(symbol, i.get("published"), i["title"], i.get("publisher"), i.get("url"), s, source)
         for i, s in zip(fresh, scores)],
    )
    return len(fresh)


def _normalise(raw: dict) -> dict:
    """yfinance news items come in two shapes depending on version."""
    c = raw.get("content", raw)
    url = (c.get("canonicalUrl") or {}).get("url") or c.get("link") or raw.get("link")
    return {
        "title": c.get("title"),
        "url": url,
        "publisher": (c.get("provider") or {}).get("displayName") or c.get("publisher"),
        "published": c.get("pubDate") or c.get("providerPublishTime"),
    }


def update_news(symbols: list[str]) -> dict[str, int]:
    import yfinance as yf

    added: dict[str, int] = {}
    with db.connect() as conn:
        for sym in symbols:
            try:
                raw = (yf.Ticker(sym).news or [])[: config.NEWS_MAX_ITEMS_PER_TICKER]
                added[sym] = store_news(conn, sym, [_normalise(r) for r in raw])
            except Exception as e:
                log.warning("News failed for %s: %s", sym, e)
                added[sym] = 0
    return added


def recent_sentiment(symbol: str | None, limit: int = 20) -> float:
    """Average sentiment of the latest headlines (used by the signal engine in step 3)."""
    with db.connect() as conn:
        rows = conn.execute("SELECT sentiment FROM news WHERE symbol IS ? AND sentiment IS NOT NULL "
                            "ORDER BY id DESC LIMIT ?", (symbol, limit)).fetchall()
    return sum(r["sentiment"] for r in rows) / len(rows) if rows else 0.0
