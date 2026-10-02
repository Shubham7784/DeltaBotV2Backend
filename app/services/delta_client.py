"""Delta Exchange API V2 Client.

Implements full REST API specification for Delta Exchange India / Global:
- Market Data (Products, Tickers, L2 Orderbook, Trades, OHLC Candles, Option Chains, Stats)
- Order Management (Single, Bracket, Batch, Edit, Cancel, Cancel All, Leverage)
- Position Management (Margined Positions, Margin Top-up, Close All)
- Wallet & Balances (Balances, Transactions, Subaccount Transfers)
- Heartbeat / Deadman Switch Management
- Dual-Domain Isolation (Separate instances for Futures vs Options subaccounts)
- Cryptographic Request Signing (HMAC-SHA256 adhering to Delta timestamp & signature rules)
"""

import hashlib
import hmac
import json
import logging
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Union

logger = logging.getLogger("delta_bot.delta_client")


class DeltaAPIError(Exception):
    """Normalized Delta Exchange API Exception."""

    def __init__(
        self,
        message: str,
        code: Optional[str] = None,
        status_code: Optional[int] = None,
        context: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(message)
        self.message = message
        self.code = code or "unknown_error"
        self.status_code = status_code
        self.context = context or {}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": False,
            "error": {
                "code": self.code,
                "message": self.message,
                "status_code": self.status_code,
                "context": self.context,
            },
        }


class DeltaClient:
    """Client for interacting with Delta Exchange REST API v2."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
        base_url: str = "https://api.india.delta.exchange",
        subaccount_name: str = "futures",
        paper_trading: bool = True,
    ):
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = base_url.rstrip("/")
        self.subaccount_name = subaccount_name
        self.paper_trading = paper_trading
        self.user_agent = "delta-bot-v2/2.0.0 (python-urllib)"
        self._ssl_context = ssl.create_default_context()

        # In-memory paper trading state when keys are absent
        self._paper_orders: List[Dict[str, Any]] = []
        self._paper_positions: Dict[str, Dict[str, Any]] = {}
        self._paper_balance_usd: float = 12.0  # Approx ₹1,000 INR baseline
        self._paper_order_counter: int = 1000

    @property
    def is_configured(self) -> bool:
        """Checks if real API credentials are configured."""
        return bool(self.api_key and self.api_secret)

    def _generate_signature(
        self,
        method: str,
        path: str,
        query_string: str,
        payload: str,
        timestamp: str,
    ) -> str:
        """Generates HMAC-SHA256 signature according to Delta Exchange specifications.

        Formula: method + timestamp + path + query_string + payload
        """
        if not self.api_secret:
            return ""

        signature_data = method + timestamp + path + query_string + payload
        secret_bytes = self.api_secret.encode("utf-8")
        message_bytes = signature_data.encode("utf-8")
        return hmac.new(secret_bytes, message_bytes, hashlib.sha256).hexdigest()

    def _request(
        self,
        method: str,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        body: Optional[Dict[str, Any]] = None,
        auth_required: bool = False,
    ) -> Dict[str, Any]:
        """Executes HTTP request to Delta Exchange with authentication and error handling."""
        method = method.upper()

        # Format query string
        query_string = ""
        clean_params: Dict[str, str] = {}
        if params:
            for k, v in params.items():
                if v is not None:
                    if isinstance(v, bool):
                        clean_params[k] = "true" if v else "false"
                    else:
                        clean_params[k] = str(v)
            if clean_params:
                query_string = "?" + urllib.parse.urlencode(clean_params)

        # Format body payload
        payload_str = ""
        if body is not None:
            payload_str = json.dumps(body, separators=(",", ":"))

        full_url = f"{self.base_url}{path}{query_string}"

        # Paper mode must never send authenticated orders to the exchange, even when keys exist.
        if auth_required and self.paper_trading:
            return self._handle_paper_fallback(method, path, params or {}, body or {})

        # If authentication is required but credentials are missing, use paper fallback when enabled.
        if auth_required and not self.is_configured:
            if self.paper_trading:
                return self._handle_paper_fallback(method, path, params or {}, body or {})
            raise DeltaAPIError(
                message=f"Credentials required for {self.subaccount_name} subaccount",
                code="missing_credentials",
                status_code=401,
            )

        timestamp = str(int(time.time()))
        headers = {
            "User-Agent": self.user_agent,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

        if auth_required or self.is_configured:
            signature = self._generate_signature(
                method=method,
                path=path,
                query_string=query_string,
                payload=payload_str,
                timestamp=timestamp,
            )
            headers["api-key"] = self.api_key or ""
            headers["timestamp"] = timestamp
            headers["signature"] = signature

        req_data = payload_str.encode("utf-8") if payload_str else None
        request = urllib.request.Request(full_url, data=req_data, headers=headers, method=method)

        try:
            with urllib.request.urlopen(request, timeout=12, context=self._ssl_context) as resp:
                resp_body = resp.read().decode("utf-8")
                if not resp_body:
                    return {"success": True, "result": None}
                data = json.loads(resp_body)
                return data
        except urllib.error.HTTPError as http_err:
            raw_err = http_err.read().decode("utf-8")
            err_code = "http_error"
            err_msg = str(http_err)
            err_context: Dict[str, Any] = {}

            try:
                err_json = json.loads(raw_err)
                if isinstance(err_json.get("error"), dict):
                    err_code = err_json["error"].get("code", err_code)
                    err_context = err_json["error"].get("context", {})
                    err_msg = err_json["error"].get("message", err_msg)
                elif isinstance(err_json.get("error"), str):
                    err_code = err_json["error"]
                    err_msg = err_json.get("message", raw_err)
            except Exception:
                err_msg = raw_err or str(http_err)

            logger.error(
                f"[DeltaClient:{self.subaccount_name}] HTTP {http_err.code} on {method} {path}: {err_code} - {err_msg}"
            )
            raise DeltaAPIError(
                message=err_msg,
                code=err_code,
                status_code=http_err.code,
                context=err_context,
            ) from http_err
        except urllib.error.URLError as url_err:
            logger.error(f"[DeltaClient:{self.subaccount_name}] Connection error on {full_url}: {url_err}")
            raise DeltaAPIError(
                message=f"Failed to connect to Delta Exchange: {url_err.reason}",
                code="network_error",
                status_code=503,
            ) from url_err
        except json.JSONDecodeError as json_err:
            logger.error(f"[DeltaClient:{self.subaccount_name}] Non-JSON response from Delta: {json_err}")
            raise DeltaAPIError(
                message="Invalid JSON response received from Delta Exchange",
                code="invalid_response",
                status_code=502,
            ) from json_err

    def _handle_paper_fallback(
        self,
        method: str,
        path: str,
        params: Dict[str, Any],
        body: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Provides simulated local execution when running in Paper Trading mode without live keys."""
        logger.info(f"[PaperEngine:{self.subaccount_name}] Simulating {method} {path}")

        # 1. Wallet Balances
        if path == "/v2/wallet/balances":
            return {
                "success": True,
                "result": [
                    {
                        "asset_id": 14,
                        "asset_symbol": "USD",
                        "balance": f"{self._paper_balance_usd:.2f}",
                        "available_balance": f"{self._paper_balance_usd * 0.70:.2f}",
                        "blocked_margin": f"{self._paper_balance_usd * 0.30:.2f}",
                        "commission": "0.00",
                        "portfolio_margin": "0.00",
                        "position_margin": "0.00",
                        "user_id": 999999,
                    }
                ],
                "meta": {
                    "net_equity": f"{self._paper_balance_usd:.2f}",
                    "paper_mode": True,
                    "subaccount": self.subaccount_name,
                },
            }

        # 2. Margined Positions
        if path in ("/v2/positions/margined", "/v2/positions"):
            open_positions = list(self._paper_positions.values())
            return {"success": True, "result": open_positions}

        # 3. Active Orders
        if path == "/v2/orders" and method == "GET":
            open_orders = [o for o in self._paper_orders if o.get("state") == "open"]
            return {"success": True, "result": open_orders, "meta": {"after": None, "before": None}}

        if method == "GET" and path.startswith("/v2/orders/client_order_id/"):
            client_order_id = urllib.parse.unquote(path.rsplit("/", 1)[-1])
            order = next((o for o in self._paper_orders if o.get("client_order_id") == client_order_id), None)
            if order is None:
                raise DeltaAPIError("Order not found", code="not_found", status_code=404)
            return {"success": True, "result": order}

        if method == "GET" and path.startswith("/v2/orders/"):
            order_id = path.rsplit("/", 1)[-1]
            order = next((o for o in self._paper_orders if str(o.get("id")) == order_id), None)
            if order is None:
                raise DeltaAPIError("Order not found", code="not_found", status_code=404)
            return {"success": True, "result": order}

        # 4. Place Order
        if path == "/v2/orders" and method == "POST":
            self._paper_order_counter += 1
            order_id = self._paper_order_counter
            client_oid = body.get("client_order_id") or f"paper_{int(time.time())}_{order_id}"
            product_symbol = body.get("product_symbol") or f"PROD_{body.get('product_id', 27)}"

            simulated_order = {
                "id": order_id,
                "user_id": 999999,
                "size": body.get("size", 1),
                "unfilled_size": 0 if body.get("order_type") == "market_order" else body.get("size", 1),
                "side": body.get("side", "buy"),
                "order_type": body.get("order_type", "market_order"),
                "limit_price": body.get("limit_price"),
                "stop_price": body.get("stop_price"),
                "state": "closed" if body.get("order_type") == "market_order" else "open",
                "client_order_id": client_oid,
                "product_id": body.get("product_id", 27),
                "product_symbol": product_symbol,
                "bracket_order": bool(body.get("bracket_take_profit_price") or body.get("bracket_stop_loss_price")),
                "bracket_take_profit_price": body.get("bracket_take_profit_price"),
                "bracket_stop_loss_price": body.get("bracket_stop_loss_price"),
                "created_at": str(int(time.time() * 1_000_000)),
                "paper_simulated": True,
            }

            self._paper_orders.append(simulated_order)

            # If market order, simulate position creation
            if body.get("order_type") == "market_order":
                self._paper_positions[product_symbol] = {
                    "product_id": body.get("product_id", 27),
                    "product_symbol": product_symbol,
                    "size": body.get("size", 1) if body.get("side") == "buy" else -body.get("size", 1),
                    "entry_price": body.get("limit_price") or "67500.00",
                    "margin": "2.50",
                    "liquidation_price": "62000.00",
                    "unrealized_pnl": "0.00",
                    "margin_mode": "isolated",
                    "paper_simulated": True,
                }

            return {"success": True, "result": simulated_order}

        # 5. Cancel Order
        if path == "/v2/orders" and method == "DELETE":
            oid = body.get("id")
            for order in self._paper_orders:
                if order.get("id") == oid or order.get("client_order_id") == body.get("client_order_id"):
                    order["state"] = "cancelled"
                    return {"success": True, "result": order}
            return {"success": True, "result": {"id": oid, "state": "cancelled"}}

        # 6. Close All Positions
        if path == "/v2/positions/close_all":
            product_symbol = body.get("product_symbol")
            if product_symbol:
                self._paper_positions.pop(product_symbol, None)
                message = f"Simulated position closed for {product_symbol}"
            else:
                self._paper_positions.clear()
                message = "All simulated positions closed"
            return {"success": True, "message": message}

        # 7. Cancel All Orders
        if path == "/v2/orders/all":
            for order in self._paper_orders:
                if order.get("state") == "open":
                    order["state"] = "cancelled"
            return {"success": True}

        # 8. Heartbeat & Rate Limits
        if "/heartbeat" in path:
            return {"success": True, "result": {"heartbeat_id": body.get("heartbeat_id", "paper_hb"), "status": "active"}}

        return {"success": True, "result": {}, "simulated": True}

    # =========================================================================
    # PUBLIC MARKET DATA ENDPOINTS (No Authentication Required)
    # =========================================================================

    def get_products(
        self,
        contract_types: Optional[str] = None,
        states: Optional[str] = "live",
        page_size: int = 100,
        after: Optional[str] = None,
        before: Optional[str] = None,
        expiry: Optional[str] = None,
    ) -> Dict[str, Any]:
        """GET /v2/products - Returns list of trading instruments."""
        params = {
            "contract_types": contract_types,
            "states": states,
            "page_size": page_size,
            "after": after,
            "before": before,
            "expiry": expiry,
        }
        return self._request("GET", "/v2/products", params=params, auth_required=False)

    def get_product_by_symbol(self, symbol: str) -> Dict[str, Any]:
        """GET /v2/products/{symbol} - Retrieves single product specification."""
        clean_symbol = symbol.strip().upper()
        return self._request("GET", f"/v2/products/{clean_symbol}", auth_required=False)

    def get_tickers(
        self,
        contract_types: Optional[str] = None,
        underlying_asset_symbols: Optional[str] = None,
        expiry_date: Optional[str] = None,
    ) -> Dict[str, Any]:
        """GET /v2/tickers - Live tickers for all or filtered products."""
        params = {
            "contract_types": contract_types,
            "underlying_asset_symbols": underlying_asset_symbols,
            "expiry_date": expiry_date,
        }
        return self._request("GET", "/v2/tickers", params=params, auth_required=False)

    def get_ticker_by_symbol(self, symbol: str) -> Dict[str, Any]:
        """GET /v2/tickers/{symbol} - Ticker details for specific symbol(s)."""
        clean_symbol = symbol.strip().upper()
        try:
            return self._request("GET", f"/v2/tickers/{clean_symbol}", auth_required=False)
        except DeltaAPIError as error:
            # Some testnet deployments return 500 for the symbol-specific route
            # even though the same ticker is available in the collection endpoint.
            if error.status_code != 500:
                raise
            tickers = self.get_tickers()
            result = tickers.get("result", [])
            if isinstance(result, list):
                for ticker in result:
                    if isinstance(ticker, dict) and ticker.get("symbol") == clean_symbol:
                        return {"success": True, "result": ticker}
            raise

    def get_option_chain(
        self,
        underlying_asset: str = "BTC",
        expiry_date: Optional[str] = None,
    ) -> Dict[str, Any]:
        """GET /v2/tickers?contract_types=call_options,put_options... - Option chain."""
        params = {
            "contract_types": "call_options,put_options",
            "underlying_asset_symbols": underlying_asset.upper(),
            "expiry_date": expiry_date,
        }
        return self._request("GET", "/v2/tickers", params=params, auth_required=False)

    def get_l2_orderbook(self, symbol: str, depth: int = 15) -> Dict[str, Any]:
        """GET /v2/l2orderbook/{symbol} - L2 orderbook depth."""
        clean_symbol = symbol.strip().upper()
        return self._request("GET", f"/v2/l2orderbook/{clean_symbol}", params={"depth": depth}, auth_required=False)

    def get_trades(self, symbol: str) -> Dict[str, Any]:
        """GET /v2/trades/{symbol} - Recent public market trades."""
        clean_symbol = symbol.strip().upper()
        return self._request("GET", f"/v2/trades/{clean_symbol}", auth_required=False)

    def get_candles(
        self,
        symbol: str,
        resolution: str = "5m",
        start: Optional[int] = None,
        end: Optional[int] = None,
    ) -> Dict[str, Any]:
        """GET /v2/history/candles - Historical OHLC candles (up to 2000 per call)."""
        now = int(time.time())
        # Default start to 6 hours ago if not specified
        if end is None:
            end = now
        if start is None:
            # 6 hours of 5m candles = 72 candles
            start = end - (6 * 3600)

        params = {
            "resolution": resolution,
            "symbol": symbol.strip().upper(),
            "start": start,
            "end": end,
        }
        return self._request("GET", "/v2/history/candles", params=params, auth_required=False)

    def get_sparklines(self, symbols: str) -> Dict[str, Any]:
        """GET /v2/history/sparklines - Compact sparkline charts."""
        return self._request("GET", "/v2/history/sparklines", params={"symbols": symbols}, auth_required=False)

    def get_rate_limit_quota(self) -> Dict[str, Any]:
        """GET /v2/rate_limits/quota - Current quota and reset time."""
        return self._request("GET", "/v2/rate_limits/quota", auth_required=False)

    def get_indices(self) -> Dict[str, Any]:
        """GET /v2/indices - Underlying spot price indices."""
        return self._request("GET", "/v2/indices", auth_required=False)

    def get_assets(self) -> Dict[str, Any]:
        """GET /v2/assets - Available cryptocurrencies and currencies."""
        return self._request("GET", "/v2/assets", auth_required=False)

    def get_stats(self) -> Dict[str, Any]:
        """GET /v2/stats - Platform 24h/7d/30d volume statistics."""
        return self._request("GET", "/v2/stats", auth_required=False)

    # =========================================================================
    # AUTHENTICATED ACCOUNT & TRADING ENDPOINTS
    # =========================================================================

    def get_wallet_balances(self) -> Dict[str, Any]:
        """GET /v2/wallet/balances - Wallet balances across assets."""
        return self._request("GET", "/v2/wallet/balances", auth_required=True)

    def get_wallet_transactions(
        self,
        asset_ids: Optional[str] = None,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
        page_size: int = 20,
    ) -> Dict[str, Any]:
        """GET /v2/wallet/transactions - Deposit, withdrawal, PnL & funding ledger."""
        params = {
            "asset_ids": asset_ids,
            "start_time": start_time,
            "end_time": end_time,
            "page_size": page_size,
        }
        return self._request("GET", "/v2/wallet/transactions", params=params, auth_required=True)

    def get_positions(
        self,
        product_id: Optional[int] = None,
        underlying_asset_symbol: Optional[str] = None,
        margined: bool = False,
    ) -> Dict[str, Any]:
        """GET /v2/positions - Real-time position size and entry price."""
        if margined:
            return self.get_margined_positions(product_ids=str(product_id) if product_id else None)
        params = {
            "product_id": product_id,
            "underlying_asset_symbol": underlying_asset_symbol,
        }
        return self._request("GET", "/v2/positions", params=params, auth_required=True)

    def get_margined_positions(
        self,
        product_ids: Optional[str] = None,
        contract_types: Optional[str] = None,
    ) -> Dict[str, Any]:
        """GET /v2/positions/margined - Detailed position information including margin and liquidation."""
        params = {
            "product_ids": product_ids,
            "contract_types": contract_types,
        }
        return self._request("GET", "/v2/positions/margined", params=params, auth_required=True)

    def change_position_margin(self, product_id: int, delta_margin: str) -> Dict[str, Any]:
        """POST /v2/positions/change_margin - Add or remove margin on an isolated position."""
        body = {"product_id": product_id, "delta_margin": str(delta_margin)}
        return self._request("POST", "/v2/positions/change_margin", body=body, auth_required=True)

    def close_position(
        self,
        symbol: Optional[str] = None,
        product_symbol: Optional[str] = None,
        product_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """POST /v2/positions/close_all - Closes an open position by product or symbol."""
        sym = product_symbol or symbol
        body: Dict[str, Any] = {"close_all_portfolio": False, "close_all_isolated": True}
        if product_id is not None:
            body["product_id"] = product_id
        if sym is not None:
            body["product_symbol"] = sym
        return self._request("POST", "/v2/positions/close_all", body=body, auth_required=True)

    def close_all_positions(
        self,
        close_all_portfolio: bool = True,
        close_all_isolated: bool = True,
        user_id: int = 0,
        product_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """POST /v2/positions/close_all - Emergency kill-switch closing all open positions."""
        body: Dict[str, Any] = {
            "close_all_portfolio": close_all_portfolio,
            "close_all_isolated": close_all_isolated,
            "user_id": user_id,
        }
        if product_id is not None:
            body["product_id"] = product_id
        return self._request("POST", "/v2/positions/close_all", body=body, auth_required=True)

    def set_auto_topup(self, product_id: int, auto_topup: bool = False) -> Dict[str, Any]:
        """PUT /v2/positions/auto_topup - Toggles position auto margin top-up."""
        body = {"product_id": product_id, "auto_topup": auto_topup}
        return self._request("PUT", "/v2/positions/auto_topup", body=body, auth_required=True)

    def place_order(
        self,
        size: int,
        side: str,
        order_type: str = "market_order",
        product_id: Optional[int] = None,
        product_symbol: Optional[str] = None,
        symbol: Optional[str] = None,
        limit_price: Optional[str] = None,
        stop_order_type: Optional[str] = None,
        stop_price: Optional[str] = None,
        trail_amount: Optional[str] = None,
        stop_trigger_method: str = "last_traded_price",
        bracket_stop_loss: Optional[float] = None,
        bracket_stop_loss_price: Optional[str] = None,
        bracket_stop_loss_limit_price: Optional[str] = None,
        bracket_take_profit: Optional[float] = None,
        bracket_take_profit_price: Optional[str] = None,
        bracket_take_profit_limit_price: Optional[str] = None,
        client_order_id: Optional[str] = None,
        reduce_only: bool = False,
        time_in_force: str = "gtc",
    ) -> Dict[str, Any]:
        """POST /v2/orders - Creates a single order (optionally with bracket TP/SL)."""
        body: Dict[str, Any] = {
            "size": int(size),
            "side": side.lower(),
            "order_type": order_type,
            "time_in_force": time_in_force,
            "reduce_only": reduce_only,
            "stop_trigger_method": stop_trigger_method,
        }

        target_sym = product_symbol or symbol
        if product_id is not None:
            body["product_id"] = product_id
        elif target_sym is not None:
            body["product_symbol"] = target_sym
        else:
            raise DeltaAPIError("Either product_id or product_symbol must be provided", code="invalid_params")

        if limit_price is not None:
            body["limit_price"] = str(limit_price)
        if stop_order_type is not None:
            body["stop_order_type"] = stop_order_type
        if stop_price is not None:
            body["stop_price"] = str(stop_price)
        if trail_amount is not None:
            body["trail_amount"] = str(trail_amount)

        # Bracket parameters
        actual_sl = bracket_stop_loss_price or (str(bracket_stop_loss) if bracket_stop_loss is not None else None)
        actual_tp = bracket_take_profit_price or (str(bracket_take_profit) if bracket_take_profit is not None else None)

        if actual_sl is not None or actual_tp is not None:
            body["bracket_stop_trigger_method"] = stop_trigger_method
            if actual_sl:
                body["bracket_stop_loss_price"] = str(actual_sl)
            if bracket_stop_loss_limit_price:
                body["bracket_stop_loss_limit_price"] = str(bracket_stop_loss_limit_price)
            if actual_tp:
                body["bracket_take_profit_price"] = str(actual_tp)
            if bracket_take_profit_limit_price:
                body["bracket_take_profit_limit_price"] = str(bracket_take_profit_limit_price)

        if client_order_id is not None:
            body["client_order_id"] = client_order_id[:32]

        return self._request("POST", "/v2/orders", body=body, auth_required=True)

    def cancel_order(
        self,
        order_id: Optional[int] = None,
        client_order_id: Optional[str] = None,
        product_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """DELETE /v2/orders - Cancels an active resting order."""
        body: Dict[str, Any] = {}
        if order_id is not None:
            body["id"] = int(order_id)
        if client_order_id is not None:
            body["client_order_id"] = client_order_id
        if product_id is not None:
            body["product_id"] = product_id

        return self._request("DELETE", "/v2/orders", body=body, auth_required=True)

    def edit_order(
        self,
        order_id: int,
        product_id: Optional[int] = None,
        product_symbol: Optional[str] = None,
        size: Optional[int] = None,
        limit_price: Optional[str] = None,
        stop_price: Optional[str] = None,
        trail_amount: Optional[str] = None,
    ) -> Dict[str, Any]:
        """PUT /v2/orders - Modifies an existing open order."""
        body: Dict[str, Any] = {"id": order_id}
        if product_id is not None:
            body["product_id"] = product_id
        elif product_symbol is not None:
            body["product_symbol"] = product_symbol
        if size is not None:
            body["size"] = size
        if limit_price is not None:
            body["limit_price"] = str(limit_price)
        if stop_price is not None:
            body["stop_price"] = str(stop_price)
        if trail_amount is not None:
            body["trail_amount"] = str(trail_amount)

        return self._request("PUT", "/v2/orders", body=body, auth_required=True)

    def get_active_orders(
        self,
        product_ids: Optional[str] = None,
        states: str = "open,pending",
        contract_types: Optional[str] = None,
        page_size: int = 50,
    ) -> Dict[str, Any]:
        """GET /v2/orders - Retrieves active orders."""
        params = {
            "product_ids": product_ids,
            "states": states,
            "contract_types": contract_types,
            "page_size": page_size,
        }
        return self._request("GET", "/v2/orders", params=params, auth_required=True)

    def get_order_by_id(self, order_id: int) -> Dict[str, Any]:
        """GET /v2/orders/{order_id} - Retrieves order by ID."""
        return self._request("GET", f"/v2/orders/{order_id}", auth_required=True)

    def get_order_by_client_oid(self, client_oid: str) -> Dict[str, Any]:
        """GET /v2/orders/client_order_id/{client_oid} - Retrieves order by custom client ID."""
        return self._request("GET", f"/v2/orders/client_order_id/{client_oid}", auth_required=True)

    def cancel_all_orders(
        self,
        product_id: Optional[int] = None,
        contract_types: Optional[str] = None,
        cancel_limit_orders: bool = True,
        cancel_stop_orders: bool = True,
        cancel_reduce_only_orders: bool = True,
    ) -> Dict[str, Any]:
        """DELETE /v2/orders/all - Cancels all open orders."""
        body: Dict[str, Any] = {
            "cancel_limit_orders": cancel_limit_orders,
            "cancel_stop_orders": cancel_stop_orders,
            "cancel_reduce_only_orders": cancel_reduce_only_orders,
        }
        if product_id is not None:
            body["product_id"] = product_id
        if contract_types is not None:
            body["contract_types"] = contract_types

        return self._request("DELETE", "/v2/orders/all", body=body, auth_required=True)

    def place_bracket_order(
        self,
        product_id: Optional[int] = None,
        product_symbol: Optional[str] = None,
        stop_loss_price: Optional[str] = None,
        stop_loss_limit: Optional[str] = None,
        take_profit_price: Optional[str] = None,
        take_profit_limit: Optional[str] = None,
        bracket_stop_trigger_method: str = "last_traded_price",
    ) -> Dict[str, Any]:
        """POST /v2/orders/bracket - Sets or updates bracket TP/SL for an open position."""
        body: Dict[str, Any] = {"bracket_stop_trigger_method": bracket_stop_trigger_method}
        if product_id is not None:
            body["product_id"] = product_id
        elif product_symbol is not None:
            body["product_symbol"] = product_symbol

        if stop_loss_price is not None:
            body["stop_loss_order"] = {
                "order_type": "limit_order" if stop_loss_limit else "market_order",
                "stop_price": str(stop_loss_price),
            }
            if stop_loss_limit:
                body["stop_loss_order"]["limit_price"] = str(stop_loss_limit)

        if take_profit_price is not None:
            body["take_profit_order"] = {
                "order_type": "limit_order" if take_profit_limit else "market_order",
                "stop_price": str(take_profit_price),
            }
            if take_profit_limit:
                body["take_profit_order"]["limit_price"] = str(take_profit_limit)

        return self._request("POST", "/v2/orders/bracket", body=body, auth_required=True)

    def set_order_leverage(self, product_id: int, leverage: int) -> Dict[str, Any]:
        """POST /v2/products/{product_id}/orders/leverage - Updates leverage setting for an instrument."""
        body = {"leverage": int(leverage)}
        return self._request("POST", f"/v2/products/{product_id}/orders/leverage", body=body, auth_required=True)

    def get_order_leverage(self, product_id: int) -> Dict[str, Any]:
        """GET /v2/products/{product_id}/orders/leverage - Gets currently set leverage."""
        return self._request("GET", f"/v2/products/{product_id}/orders/leverage", auth_required=True)

    def get_order_history(
        self,
        product_ids: Optional[str] = None,
        contract_types: Optional[str] = None,
        page_size: int = 20,
    ) -> Dict[str, Any]:
        """GET /v2/orders/history - History of closed and cancelled orders."""
        params = {
            "product_ids": product_ids,
            "contract_types": contract_types,
            "page_size": page_size,
        }
        return self._request("GET", "/v2/orders/history", params=params, auth_required=True)

    def get_fills(
        self,
        product_ids: Optional[str] = None,
        contract_types: Optional[str] = None,
        page_size: int = 20,
    ) -> Dict[str, Any]:
        """GET /v2/fills - Trade fill executions."""
        params = {
            "product_ids": product_ids,
            "contract_types": contract_types,
            "page_size": page_size,
        }
        return self._request("GET", "/v2/fills", params=params, auth_required=True)

    def get_sub_accounts(self) -> Dict[str, Any]:
        """GET /v2/sub_accounts - Lists subaccounts attached to user account."""
        return self._request("GET", "/v2/sub_accounts", auth_required=True)

    def set_margin_mode(self, margin_mode: str = "isolated", subaccount_user_id: Optional[str] = None) -> Dict[str, Any]:
        """PUT /v2/users/margin_mode - Sets margin mode (isolated or portfolio)."""
        body: Dict[str, Any] = {"margin_mode": margin_mode}
        if subaccount_user_id:
            body["subaccount_user_id"] = subaccount_user_id
        return self._request("PUT", "/v2/users/margin_mode", body=body, auth_required=True)

    # =========================================================================
    # DEADMAN SWITCH / HEARTBEAT ENDPOINTS
    # =========================================================================

    def create_heartbeat(
        self,
        heartbeat_id: str,
        impact: str = "contracts",
        contract_types: Optional[List[str]] = None,
        product_symbols: Optional[List[str]] = None,
        unhealthy_count: int = 1,
    ) -> Dict[str, Any]:
        """POST /v2/heartbeat/create - Registers deadman switch with Delta Exchange."""
        body: Dict[str, Any] = {
            "heartbeat_id": heartbeat_id,
            "impact": impact,
            "config": [{"action": "cancel_orders", "unhealthy_count": unhealthy_count}],
        }
        if contract_types:
            body["contract_types"] = contract_types
        if product_symbols:
            body["product_symbols"] = product_symbols

        return self._request("POST", "/v2/heartbeat/create", body=body, auth_required=True)

    def send_heartbeat(self, heartbeat_id: str, ttl: int = 30000) -> Dict[str, Any]:
        """POST /v2/heartbeat - Sends keepalive acknowledgment (TTL in ms, e.g. 30000)."""
        body = {"heartbeat_id": heartbeat_id, "ttl": ttl}
        return self._request("POST", "/v2/heartbeat", body=body, auth_required=True)

    def get_heartbeats(self, user_id: Optional[int] = None, heartbeat_id: Optional[str] = None) -> Dict[str, Any]:
        """GET /v2/heartbeat - Lists registered heartbeats."""
        params: Dict[str, Any] = {}
        if user_id is not None:
            params["user_id"] = user_id
        if heartbeat_id is not None:
            params["heartbeat_id"] = heartbeat_id
        return self._request("GET", "/v2/heartbeat", params=params, auth_required=True)
