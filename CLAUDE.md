# Vigil — MNQ Pre-Market Regime Classifier

## What This Project Is
Vigil is a forward-looking pre-market intelligence system for MNQ futures.
It classifies each trading day into a regime (Trending Low Vol, Trending High Vol,
Mean Reverting, Chaotic) and outputs a GREEN / YELLOW / RED verdict before the open.

It is NOT a signal generator. It is NOT a backtest engine.
It tells you when the edge exists — you make the trade.

## Stack
- **Backend:** Python, FastAPI, APScheduler
- **Data:** yfinance, Tradier API, Finnhub API
- **ML:** FinBERT (ProsusAI/finbert), hmmlearn HMM
- **Database:** Supabase (Postgres)
- **Deployment:** Railway ($5/mo), Vercel (free)
- **Alerts:** Telegram bot
- **Frontend:** Next.js (inline styles only, no Tailwind)

## Folder Structure
main.py                  # FastAPI app + APScheduler entry point
api/routes.py            # All API endpoints
data/fetcher.py          # yfinance data fetching + calculations
data/sentiment.py        # Finnhub news + FinBERT scoring
data/options.py          # Tradier IV + GEX calculation
classifier/scorer.py     # Strike engine + semiconductor health score
classifier/regime.py     # HMM regime detection (weekly retrain)
classifier/range_model.py # Forward-looking expected range model
alerts/telegram.py       # Morning alert + intraday VIX spike alerts
ui/dashboard.py          # Rich terminal dashboard
classifier-dashboard/    # Next.js Vercel frontend

## Environment Variables
All keys stored in .env — never commit this file.
See .env.example for required keys:
SUPABASE_URL, SUPABASE_KEY,
FINNHUB_API_KEY, TRADIER_API_KEY,
TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID,
TZ=America/New_York

## How It Runs
- 8:00 AM ET Monday: HMM retrains on 2 years of rolling data
- 8:45 AM ET weekdays: full pipeline runs automatically
- 8:47 AM ET: Telegram morning alert fires
- 9:30 AM — 4:00 PM ET: VIX checked every 15 mins for regime shifts

## Run Commands
python main.py            # Run once manually
python main.py --live     # Refresh dashboard every 60 seconds
python main.py --test     # Run with mock data (no market hours needed)

## API Endpoints
POST /run                 # Trigger full pipeline manually
GET  /latest              # Today's verdict JSON
GET  /history?days=30     # Last N verdicts from Supabase
GET  /health              # API connectivity + last run status

## Scoring Logic
Strikes are accumulated across four categories:
- Technical (yield, VIX, SMH vs SPY/QQQ, semi health, IV)
- Sentiment (FinBERT semi + macro scores, guidance cuts)
- Event risk (CPI, NFP, FOMC, GDP scheduled today)
- Regime adjustment (HMM output modifies final strike count)

Verdict:
- 0-1 strikes = GREEN (trade full plan)
- 2 strikes   = YELLOW (size down or skip)
- 3+ strikes  = RED (stay out)

Automatic RED overrides bypass strike count entirely.

## Key Rules for Claude Code
- Never introduce backtesting logic — this system is forward-looking only
- Never save a static trained HMM model — retrain on rolling data every Monday
- All Next.js styling must use inline styles only — no Tailwind, no CSS modules
- All async database writes use independent AsyncSession (never reuse request session)
- If any API call fails, fall back gracefully and label data as CACHED or ESTIMATED
- Never store real values in .env.example — placeholder strings only
- Keep the scoring engine in scorer.py only — do not split strike logic across files
- FinBERT model downloads once on first boot and caches to Railway volume

## Data Sources
| Data              | Source         | Notes                        |
|-------------------|----------------|------------------------------|
| Yield, VIX, Semis | yfinance       | 15min delayed, fine for bias |
| SMH/NVDA IV       | Tradier API    | Free paper account           |
| GEX               | Tradier SPY    | options chain                |
| Overnight news    | Finnhub API    | Free tier, 60 calls/min      |
| Sentiment scoring | FinBERT local  | Runs on Railway               |

## What Good Output Looks Like
"🟢 GREEN — Trending Low Vol (87%)
 Range: 19,840 — 19,960 (1σ)
 GEX: Negative — moves will amplify
 Semis: Strong (74/100)
 Yield: +2bps, stable
 No event risk
 Approach: trend following, hold runners"