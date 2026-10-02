# Delta Bot V2 — Automated Trading Platform for Delta Exchange

Delta Bot V2 is a purpose-built algorithmic trading engine designed for **Delta Exchange** (India and Global). It provides dual-domain separation between **Futures** and **Options**, micro-wallet capital preservation (₹1,000 INR baseline), deterministic risk management, and a strict 7-state trade execution lifecycle.

---

## Key Capabilities

- **Delta Exchange API v2 Integration**:
  - Full REST API client with **HMAC-SHA256** request signing.
  - Live public market data feeds: Tickers, L2 Orderbook, OHLC Historical Candles, Recent Trades, Option Chains with Greeks.
  - Private Account & Execution endpoints: Wallet balances, transaction ledger, active orders, positions, margin adjustments, leverage control, and Deadman switch heartbeats.
- **Dual-Domain Subaccount Architecture**:
  - Independent API keys and secrets for Futures (`DELTA_FUTURES_API_KEY`) and Options (`DELTA_OPTIONS_API_KEY`).
  - Isolated margin pools: drawdowns in Futures never impact Options capital.
- **Capital Preservation on Micro-Wallets**:
  - Designed for a ₹1,000 INR (~$12.00 USD) baseline capital.
  - 30% Safety Reserve buffer held off the table at all times.
  - 50% Maximum Capital Exposure limit.
  - Max 1% risk per individual trade.
- **Strict Invariant: Max 1 Active Future Position Per Instrument**:
  - Multi-timeframe signals (5m, 15m, 1h) aggregate into a single decision per asset.
  - Opposing (hedged) or duplicate positions on the same asset are strictly forbidden.
- **Deterministic 7-State Trade Lifecycle**:
  - `SIGNAL_GENERATED` → `VALIDATING` → `LLM_CONFIRMED` → `RISK_VALIDATED` → `ORDER_SUBMITTED` → `POSITION_OPEN` → `TP/SL EXIT`.
  - The LLM acts purely as an advisory confirmation layer and cannot override stop-loss or risk limits.
- **Built-in Paper Trading & Simulation**:
  - Test all execution flows, bracket orders, and positions locally without live exchange risk.

---

## Project Structure

```text
backend/       FastAPI application, API routes, and Python services
src/           React frontend (TypeScript)
server.py      Local runner for the FastAPI backend
vite.config.ts Vite frontend development and build configuration
```

## Detailed Documentation

For a comprehensive explanation of the signature algorithms, endpoint parameters, rate limits, and the 7-state trade lifecycle, see:

📖 **[docs/WORKFLOW_AND_DELTA_API.md](docs/WORKFLOW_AND_DELTA_API.md)**

---

## Getting Started

### Local Development

Configure backend settings and Delta API credentials in `.env` at the repository root. Install dependencies:

```bash
npm install
pip install -r backend/requirements.txt
```

Run the backend and frontend in separate terminals:

```bash
python server.py
```

```bash
npm run dev
```

Vite proxies `/api` and WebSocket traffic to `http://localhost:8000`. Set `BACKEND_URL` to change that local proxy target.

### Deploy the Backend to Railway

Create a Railway service from this repository, leave its root directory as `/`, and set its config file path to `backend/railway.json`. The service installs `backend/requirements.txt` and runs FastAPI on Railway's `PORT`.

Set backend environment variables in Railway, including Delta credentials and `DATABASE_URL` if using a managed database. Set `FRONTEND_ORIGIN` to the deployed frontend origin (for example, `https://your-app.vercel.app`) to restrict browser API access to that site.

### Deploy the Frontend to Vercel

Import the same repository as a Vercel project. `vercel.json` builds the Vite app into `dist`. Configure this build environment variable in Vercel:

```text
VITE_API_BASE_URL=https://your-backend.up.railway.app
```

Use the backend's public origin without a trailing slash. Vite embeds this value when it builds the frontend, so redeploy after changing it.

Other static hosting platforms can use `npm ci`, `npm run build`, and `dist` as the output directory, with the same `VITE_API_BASE_URL` build variable.
