# Live Scalp Bot - Real Data Only

Multi-agent scalping signals: Stocks / Crypto / Forex
- No mock data. If API keys missing, returns empty.
- 24/7 on Railway

## Deploy to Railway
1. Push to GitHub
2. Railway -> New Project -> Deploy from GitHub
3. Add env vars:
   ALPACA_API_KEY=
   ALPACA_SECRET_KEY=
   BINANCEUS_API_KEY=
   BINANCEUS_SECRET_KEY=
   OANDA_API_KEY=
   OANDA_ACCOUNT_ID=

## Health Check
GET /health -> basic
GET /api/health/full -> tests all API keys with real calls
