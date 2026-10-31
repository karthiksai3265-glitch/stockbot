# StockBot: automated stock analysis

Research and recommendation tool for S&P 500, ASX 200 and Nifty 50 (NSE India) stocks. NSE data comes via
Yahoo Finance (`.NS` tickers); nseindia.com has no free official API and blocks scraping, so it is not used directly. **Manual execution by default**: it never
connects to a broker unless you turn on one-tap approval (see below), and even then nothing is sent without your
approval of each order. Not financial advice.

## Quick start: the working website (no Node needed)
```bash
cd backend
pip install -r requirements.txt
python -m app.serve --demo          # opens http://localhost:8000 with FICTIONAL demo data (about 15 s to build the first time)
python -m app.serve --schedule      # REAL data: fetches on first start, then updates itself weekdays at 07:30 and 17:30
```
- `--schedule` needs internet access to Yahoo Finance and takes a while on the first run (about two years of prices for ~1,000 stocks). Use the **Update now** button, or `python -m app.run_daily`, to refresh by hand.
- Change times and timezone with `STOCKBOT_RUN_TIMES=07:30,17:30` and `STOCKBOT_TZ=Australia/Sydney` (in `backend/.env`).
- The server listens on this computer only. For one-tap broker approval set `STOCKBOT_BROKER=alpaca`, the Alpaca keys and a long random `STOCKBOT_APPROVE_TOKEN` in `.env`, then type the token into the Orders page.
- `python -m app.serve` with no flags serves real data without automatic updates.

What was verified: all pages render (desktop and mobile, light and dark) against the demo data, and 67 offline tests pass. What was NOT verified: a live run on real Yahoo data, the live Alpaca service, and the optional `frontend/` (Next.js) site, which was never built and is not needed.

## Free hosted website that updates itself (GitHub Pages)
1. Create a free GitHub account and a new **private or public** repository, and upload this folder to it.
2. Repository **Settings > Pages > Source: GitHub Actions**.
3. **Actions > Update StockBot site > Run workflow**. The first run downloads two years of prices and can take 30 to 60 minutes. After that it runs by itself twice each weekday and the page link appears in the run.
4. Optional: add an `ANTHROPIC_API_KEY` repository secret for AI news scoring.
The hosted page is read-only (no order buttons). It has not been run against live Yahoo data from here, so check the first run's log.

## Build order
1. **Data pipeline + database**: universe, daily prices, quarterly financials, news + sentiment
2. **Backtesting framework**: `app/backtest.py`, with in-sample / out-of-sample split
3. **Recommendation engine + scheduler**: `app/engine.py`, `app/grading.py`, `app/run_daily.py`
4. **Paper portfolio**: `app/portfolio.py`; long-only, risk limits, kill switch (`python -m app.portfolio pause`)
5. **Self-improvement loop**: `app/learn.py`; weekly, gated, logged, with rollback (see below)
6. **Website**: `app/serve.py` + `app/web/index.html` (Python only; TradingView widgets embedded when online)

## Run the steps by hand
```bash
cd backend && pip install -r requirements.txt
python -m app.pipeline --limit 50      # load data (drop --limit for everything; first run is slow)
python -m app.backtest --limit 50      # test the model on history (READ THIS FIRST)
python -m app.run_daily                # refresh data, grade past calls, generate recommendations
python -m app.serve                    # website
```

## The self-improvement loop
`python -m app.run_daily` runs a learning cycle at most once a week (or run `python -m app.learn` yourself).
- It tries small single changes to the active model: technical weights, entry threshold, stop and target distance.
  After 60 graded live calls it also tries re-weighting technical / fundamental / news scores.
- Candidates are ranked on older data only. The single best one is judged ONCE on newer data it was not tuned on.
- It replaces the active model only if it has enough trades, a positive average trade after costs, a real
  improvement (at least 0.05 percentage points), statistical evidence (z of 1.28 or more), no worse drawdown, and
  at least 30 days since the last change.
- Every attempt, accepted or rejected, is stored and shown on the website's Learning log. Old versions are kept.
- If a new version's graded live calls turn out negative and worse than its parent's, it is rolled back automatically.
  Manual rollback: `python -m app.learn rollback`.
- If the active model shows no edge out-of-sample, the log says so with a warning. Believe it.

Check on pure random-walk prices (no edge exists): across 25 datasets the loop promoted nothing, rejecting changes on
the out-of-sample checks. That is the intended behaviour. Real markets can still fool it, because out-of-sample data
is reused across weekly cycles and trades in different stocks are correlated.

## How the model works
- **Technical score** (-1..+1): trend (price vs 50/200-day averages), 3-month momentum, RSI extremes.
- **Fundamental score**: sales growth, profit margin, debt vs sales, free cash flow.
- **News score**: average sentiment of recent headlines (Claude if a key is set, keyword fallback otherwise).
- **Combined** = weighted average (50/30/20) over whichever components have data. A stock is recommended when
  the combined score is strong enough; entry = latest close, stop = 2x ATR, target = 3x ATR, size = 0.5% portfolio
  risk, capped at 5%. These starting values are what the learning loop adjusts over time (each version stores its own).
- **Signal strength is not a win probability.** Every call is logged and later graded WIN/LOSS with the same rules
  as the backtest (stop assumed hit first if both touched; 20-day timeout; 0.1% cost per side).
- SELL means "exit / avoid"; many retail accounts cannot short.

## Backtest limits (important)
Only the technical score is backtested: free data cannot tell us what fundamentals/news looked like on past dates.
Survivorship bias (today's index members only) makes results look better than reality. Trust the out-of-sample
numbers only, compare against buy-and-hold, and expect the live record to be worse. If out-of-sample results are not
clearly positive after costs, do not trade the signals with real money.

## What is real vs demo on the website
Everything is real once an update has run. In `--demo` mode everything is fictional and says so in a yellow banner.
The TradingView widgets (not shown in demo mode) are TradingView's free embeds:
they need internet access in your browser and are for viewing only (TradingView offers no free data feed for the model).

## Setup
```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # optional: add ANTHROPIC_API_KEY for AI news scoring
python -m app.pipeline --limit 20   # quick test on 20 tickers
python -m app.pipeline              # full run (first run downloads ~2 years of prices; takes a while)
python -m pytest                    # offline tests
```

## Structure
```
backend/app/
  config.py            settings (env vars / .env)
  db.py                SQLite schema: tickers, prices, fundamentals, news, pipeline_runs
  pipeline.py          runs all steps; schedule it twice a day
  data/universe.py     S&P 500 + ASX 200 constituents (built-in fallback if offline)
  data/prices.py       incremental daily OHLCV via yfinance
  data/fundamentals.py quarterly sales, earnings, debt, cash flow
  data/news.py         headlines + sentiment (Claude if key set, keyword fallback otherwise)
  broker/              Broker interface + ManualBroker (default). Add real brokers here later.
```

## Configuration (.env)
| Variable | Default | Meaning |
|---|---|---|
| `STOCKBOT_UNIVERSE` | `SP500,ASX200` | Markets to track (use just one if you prefer) |
| `ANTHROPIC_API_KEY` | empty | Enables AI headline scoring; without it a simple keyword scorer is used |
| `STOCKBOT_LLM_MODEL` | `claude-sonnet-5-5` | Model for sentiment scoring |
| `STOCKBOT_BROKER` | `none` | `none` = manual execution |

## Data limitations (be aware)
- yfinance is an unofficial free source: it can be rate-limited or change without notice, and quarterly
  history is short (about 4-5 quarters). For serious use, swap in SEC EDGAR / a paid provider by replacing
  `data/fundamentals.py` and `data/prices.py`; nothing else depends on the source.
- Headline sentiment is a noisy signal. It is one input among several, never a decision by itself.

## One-tap broker approval (optional, off by default)
Supported broker: **Alpaca**, US stocks only (it cannot trade ASX or NSE stocks; those stay manual).
1. Create an Alpaca **paper** account and put the keys in `backend/.env` (see `.env.example`): `STOCKBOT_BROKER=alpaca`,
   `ALPACA_API_KEY`, `ALPACA_API_SECRET`. Paper money is the default; real money needs `STOCKBOT_ALPACA_LIVE=yes`.
2. After each daily run, `python -m app.execution propose` (run_daily does this) turns the latest recommendations into
   PENDING order tickets: whole shares, sized from your risk rules, capped by `STOCKBOT_MAX_ORDER_USD`.
3. You approve or reject each one: the website's **Orders** page, or `python -m app.execution approve 12`.
   A buy is one limit order with an attached stop-loss and take-profit. A sell closes a position you already hold.
4. Every approval re-checks: kill switch, single-order limit, orders per day, dollars per day, ticket expiry (24h),
   cash available, and paper/live mode unchanged. Failed or refused orders are recorded, never retried.
5. Kill switch: the Orders page button or `python -m app.execution halt`.

Website approval needs the same secret in `backend/.env` and `frontend/.env.local` (`STOCKBOT_APPROVE_TOKEN`). The token stays
on the server and is never sent to the browser. Cross-site requests are refused. Run the API and website on your own
computer only; do not put them on the public internet.

Honest limits: the Alpaca calls (endpoints, headers, bracket-order fields) were checked against Alpaca's documentation and
tested against a simulated server, but never run against the real service from where this was built. Test on paper first.
Entry orders are good-till-cancelled so the stop and target stay active after a fill; cancel stale unfilled ones
(`python -m app.execution cancel ID`). Do not go live until paper trading, the logged recommendation record and the
out-of-sample backtest all look good for months, and start with small limits. Most retail algorithmic trading loses money.

To add another broker, implement `broker.base.Broker` (`place_order`, `get_positions`, `get_cash`, `supports`, `mode`) and
register it in `broker/__init__.py`; the approval workflow and limits apply to it automatically.
