# Delta Bot V2 — Workflow & Delta Exchange API Integration Guide

## 1. System Overview & Core Philosophy

**Delta Bot V2** is an automated trading platform engineered specifically for **Delta Exchange** (India & Global). It is architected from the ground up for high reliability, capital preservation on small/micro-wallets (₹1,000 INR / ~$12 USD baseline), strict risk invariants, and dual-domain isolation between **Futures** and **Options**.

---

## 2. Delta Exchange API Specification & Integration

### 2.1 API Gateways

Delta Exchange provides distinct production and sandbox environments:

| Environment | REST Base URL | Public WebSocket | Private WebSocket |
| :--- | :--- | :--- | :--- |
| **India Production** | `https://api.india.delta.exchange` | `wss://public-socket.india.delta.exchange` | `wss://socket.india.delta.exchange` |
| **Global Production**| `https://api.delta.exchange` | `wss://socket.delta.exchange` | `wss://socket.delta.exchange` |
| **Testnet / Sandbox** | `https://cdn-ind.testnet.deltaex.org` | `wss://socket-ind-pub.testnet.deltaex.org` | `wss://socket-ind.testnet.deltaex.org` |

### 2.2 Authentication & Cryptographic Request Signing

Delta Exchange uses **HMAC-SHA256** for authenticating private REST API requests.

#### Signature Formula:
```
signature_data = method + timestamp + path + query_string + payload
signature = hmac_sha256_hex(api_secret, signature_data)
```

- **`method`**: Uppercase HTTP method (e.g., `GET`, `POST`, `PUT`, `DELETE`).
- **`timestamp`**: Current UTC Unix timestamp in seconds (integer string, e.g., `1710921600`).
- **`path`**: Exact endpoint request URI (e.g., `/v2/orders`, `/v2/wallet/balances`).
- **`query_string`**: If URL query parameters exist, formatted as `?key1=val1&key2=val2` (including the leading `?`). If no query parameters, this is an empty string `""`.
- **`payload`**: The raw JSON request body string (e.g., `{"product_id":27,"size":1}`). If there is no request body (as in `GET` requests), this is an empty string `""`.

#### Required Headers for Authenticated Requests:
```http
api-key: your_subaccount_api_key
timestamp: 1710921600
signature: c3de806be76b08ec3ab90ac5b6c380020a3e8c7241ead9022ba462c9584bfae6
User-Agent: delta-bot-v2/2.0.0 (node-fetch)
Content-Type: application/json
Accept: application/json
```

> **Important**: Delta Exchange Cloudflare edge requires a valid, non-standard `User-Agent`. Our client automatically sends `delta-bot-v2/2.0.0` with every call.

### 2.3 Dual-Domain Subaccount Architecture

Futures trading and Options trading operate on **completely separate subaccounts**:

```
                             ┌───────────────────────────────┐
                             │       DELTA BOT V2 CORE       │
                             └───────────────┬───────────────┘
                                             │
                     ┌───────────────────────┴───────────────────────┐
                     ▼                                               ▼
      ┌─────────────────────────────┐                 ┌─────────────────────────────┐
      │  FUTURES TRADING DOMAIN     │                 │   OPTIONS TRADING DOMAIN    │
      │  - DELTA_FUTURES_API_KEY    │                 │   - DELTA_OPTIONS_API_KEY   │
      │  - DELTA_FUTURES_API_SECRET │                 │   - DELTA_OPTIONS_API_SECRET│
      │  - Isolated Margin Pool     │                 │   - Defined-Risk Options    │
      │  - Max 1 Position / Asset   │                 │   - Greeks & IV Scanner     │
      └─────────────────────────────┘                 └─────────────────────────────┘
```

- Drawdowns, liquidation risks, or margin calls in Futures **cannot** drain or affect the capital of the Options subaccount.
- Both subaccounts have dedicated instances of `DeltaClient` configured with their respective API credentials.

### 2.4 Delta Exchange REST API Endpoints Catalog

#### Market Data (Public):
- `GET /v2/products`: List of available perpetual futures and options products.
- `GET /v2/products/{symbol}`: Specifications (tick size, contract size, leverage, initial margin) for an instrument.
- `GET /v2/tickers`: Live market tickers, mark prices, 24h volume, high/low, and best bid/ask quotes.
- `GET /v2/tickers/{symbol}`: Ticker for a specific symbol (e.g., `BTCUSD`, `ETHUSD`).
- `GET /v2/l2orderbook/{symbol}`: Level 2 orderbook depth.
- `GET /v2/trades/{symbol}`: Recent public trades executed on the exchange.
- `GET /v2/history/candles`: Historical OHLC candles (`resolution`: 1m, 5m, 15m, 1h, 1d).
- `GET /v2/rate_limits/quota`: Current rate limit consumption and reset timing.

#### Account & Position Management (Authenticated):
- `GET /v2/wallet/balances`: Subaccount wallet balances, total equity, available margin, and blocked margin.
- `GET /v2/positions`: Open positions with size, entry price, and unrealized PnL.
- `GET /v2/positions/margined`: Margined positions with liquidation price, bankruptcy price, and margin mode.
- `POST /v2/positions/change_margin`: Add or remove margin on an isolated position.
- `POST /v2/positions/close_all`: Emergency kill-switch closing all open positions.

#### Orders & Execution (Authenticated):
- `POST /v2/orders`: Creates single market or limit orders.
- `POST /v2/orders/bracket`: Attaches bracket Take-Profit and Stop-Loss triggers to a position.
- `PUT /v2/orders`: Modifies price or size of an open limit order.
- `DELETE /v2/orders`: Cancels an open order by ID or `client_order_id`.
- `DELETE /v2/orders/all`: Emergency cancellation of all open orders.
- `POST /v2/products/{product_id}/orders/leverage`: Configures leverage for a contract.

#### Deadman Switch / Heartbeat:
- `POST /v2/heartbeat/create`: Registers a deadman switch on Delta Exchange.
- `POST /v2/heartbeat`: Sends a keepalive ping with a specified TTL (e.g., 30,000 ms). If the bot crashes and fails to ping within the TTL window, Delta Exchange automatically cancels all active orders.

---

## 3. End-to-End Bot Workflow

### 3.1 7-State Trade Lifecycle

Every potential trade passes through a strict 7-state state machine:

```
[1. SIGNAL_GENERATED]
       │
       ▼  Technical filters passed (5m/15m/1h alignment, EMA 9/20, MACD, VWAP)
[2. VALIDATING]
       │
       ▼  LLM market regime analysis (news sentiment, macro bias)
[3. LLM_CONFIRMED]
       │
       ▼  Deterministic risk gate (Capital checks, 1% risk limit, 30% reserve buffer)
[4. RISK_VALIDATED]
       │
       ▼  HMAC-SHA256 signed order with bracket TP/SL submitted to Delta
[5. ORDER_SUBMITTED]
       │
       ▼  Exchange fill confirmation
[6. POSITION_OPEN]
       │
       ▼  Delta Exchange bracket execution or trailing stop hit
[7. TP/SL EXIT]
```

### 3.2 Position Invariants & Rules

1. **Max 1 Active Futures Position Per Instrument**:
   - The bot aggregates signals from 5m, 15m, and 1h timeframes into a **single consolidated decision**.
   - It will **never** open opposing (hedged) or duplicate positions on the same instrument.
2. **Capital Preservation Rules**:
   - Baseline capital: ₹1,000 INR (~$12 USD).
   - Maximum simultaneous exposure: 50% of total capital.
   - Safety reserve buffer: 30% held strictly in reserve and never deployed.
   - Maximum loss per trade: Capped at 1% of total account capital.
3. **Deterministic Risk Protection**:
   - Stop-Loss and Take-Profit calculations are 100% mathematical.
   - The LLM acts purely as an advisory confirmation layer and is **strictly prohibited** from altering or overriding risk limits, stop-loss prices, or position sizing.

---

## 4. Configuration & Environment Variables

Define the following environment variables in `.env` or project settings:

```env
# Runtime Environment
ENVIRONMENT=development
PAPER_TRADING=true
PORT=3000

# Delta Exchange API Gateway Endpoints
DELTA_API_BASE_URL=https://api.india.delta.exchange

# Subaccount 1: Futures Trading
DELTA_FUTURES_API_KEY=your_futures_api_key_here
DELTA_FUTURES_API_SECRET=your_futures_api_secret_here

# Subaccount 2: Options Trading
DELTA_OPTIONS_API_KEY=your_options_api_key_here
DELTA_OPTIONS_API_SECRET=your_options_api_secret_here

# Capital & Risk Defaults
INITIAL_WALLET_INR=1000.0
INITIAL_WALLET_USD=12.0
DEFAULT_LEVERAGE=50
DEFAULT_TP_PCT=4.0
DEFAULT_SL_PCT=1.0
```

When `PAPER_TRADING=true` (or when keys are not set), the built-in paper engine simulates order placement, margin allocation, position tracking, and bracket TP/SL behavior without risking real capital.
