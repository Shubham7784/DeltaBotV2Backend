"""Delta Exchange API v2 Router for FastAPI."""

import logging
from typing import Any, Dict, Optional
from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from app.services.client_factory import get_client
from app.services.delta_client import DeltaAPIError

logger = logging.getLogger("delta_bot.api.delta")
router = APIRouter(prefix="/delta", tags=["Delta Exchange API"])


def _extract_subaccount(request: Request) -> str:
    """Extracts target subaccount ('futures' or 'options') from query or body."""
    sub = request.query_params.get("subaccount")
    return sub if sub else "futures"


# =============================================================================
# 1. MARKET DATA (PUBLIC)
# =============================================================================

@router.get("/tickers/{symbol}")
async def get_ticker(symbol: str, request: Request):
    """GET /api/v1/delta/tickers/{symbol}"""
    client = get_client(_extract_subaccount(request))
    try:
        data = client.get_ticker_by_symbol(symbol.upper())
        return data
    except DeltaAPIError as err:
        return JSONResponse(status_code=err.status_code or 400, content=err.to_dict())
    except Exception as e:
        return JSONResponse(status_code=500, content={"success": False, "error": {"message": str(e)}})


@router.get("/tickers")
async def get_all_tickers(
    request: Request,
    contract_types: Optional[str] = Query(default=None),
    underlying_asset_symbols: Optional[str] = Query(default=None),
    expiry_date: Optional[str] = Query(default=None),
):
    """GET /api/v1/delta/tickers"""
    client = get_client(_extract_subaccount(request))
    try:
        data = client.get_tickers(
            contract_types=contract_types,
            underlying_asset_symbols=underlying_asset_symbols,
            expiry_date=expiry_date,
        )
        return data
    except DeltaAPIError as err:
        return JSONResponse(status_code=err.status_code or 400, content=err.to_dict())
    except Exception as e:
        return JSONResponse(status_code=500, content={"success": False, "error": {"message": str(e)}})


@router.get("/orderbook/{symbol}")
async def get_orderbook(symbol: str, request: Request, depth: int = Query(default=15)):
    """GET /api/v1/delta/orderbook/{symbol}"""
    client = get_client(_extract_subaccount(request))
    try:
        data = client.get_l2_orderbook(symbol.upper(), depth=depth)
        return data
    except DeltaAPIError as err:
        return JSONResponse(status_code=err.status_code or 400, content=err.to_dict())
    except Exception as e:
        return JSONResponse(status_code=500, content={"success": False, "error": {"message": str(e)}})


@router.get("/option-chain")
async def get_option_chain(
    request: Request,
    underlying: str = Query(default="BTC"),
    expiry: Optional[str] = Query(default=None),
):
    """GET /api/v1/delta/option-chain?underlying=BTC"""
    client = get_client(_extract_subaccount(request))
    try:
        data = client.get_option_chain(
            underlying_asset=underlying.upper(),
            expiry_date=expiry,
        )
        return data
    except DeltaAPIError as err:
        return JSONResponse(status_code=err.status_code or 400, content=err.to_dict())
    except Exception as e:
        return JSONResponse(status_code=500, content={"success": False, "error": {"message": str(e)}})


@router.get("/candles")
async def get_candles(
    request: Request,
    symbol: str = Query(default="BTCUSD"),
    resolution: str = Query(default="5m"),
    start: Optional[int] = Query(default=None),
    end: Optional[int] = Query(default=None),
):
    """GET /api/v1/delta/candles"""
    client = get_client(_extract_subaccount(request))
    try:
        data = client.get_candles(
            symbol=symbol.upper(),
            resolution=resolution,
            start=start,
            end=end,
        )
        return data
    except DeltaAPIError as err:
        return JSONResponse(status_code=err.status_code or 400, content=err.to_dict())
    except Exception as e:
        return JSONResponse(status_code=500, content={"success": False, "error": {"message": str(e)}})


@router.get("/rate-limits")
async def get_rate_limits(request: Request):
    """GET /api/v1/delta/rate-limits"""
    client = get_client(_extract_subaccount(request))
    try:
        data = client.get_rate_limit_quota()
        return data
    except DeltaAPIError as err:
        return JSONResponse(status_code=err.status_code or 400, content=err.to_dict())
    except Exception as e:
        return JSONResponse(status_code=500, content={"success": False, "error": {"message": str(e)}})


# =============================================================================
# 2. ACCOUNT & WALLET (AUTHENTICATED)
# =============================================================================

@router.get("/wallet/balances")
async def get_wallet_balances(request: Request):
    """GET /api/v1/delta/wallet/balances"""
    client = get_client(_extract_subaccount(request))
    try:
        data = client.get_wallet_balances()
        return data
    except DeltaAPIError as err:
        return JSONResponse(status_code=err.status_code or 400, content=err.to_dict())
    except Exception as e:
        return JSONResponse(status_code=500, content={"success": False, "error": {"message": str(e)}})


# =============================================================================
# 3. POSITIONS & MARGIN (AUTHENTICATED)
# =============================================================================

@router.get("/positions")
async def get_positions(
    request: Request,
    margined: bool = Query(default=True),
    product_id: Optional[int] = Query(default=None),
    underlying_asset_symbol: Optional[str] = Query(default=None),
):
    """GET /api/v1/delta/positions"""
    client = get_client(_extract_subaccount(request))
    try:
        data = client.get_positions(
            product_id=product_id,
            underlying_asset_symbol=underlying_asset_symbol,
            margined=margined,
        )
        return data
    except DeltaAPIError as err:
        return JSONResponse(status_code=err.status_code or 400, content=err.to_dict())
    except Exception as e:
        return JSONResponse(status_code=500, content={"success": False, "error": {"message": str(e)}})


@router.get("/positions/margined")
async def get_margined_positions(
    request: Request,
    product_ids: Optional[str] = Query(default=None),
    contract_types: Optional[str] = Query(default=None),
):
    """GET /api/v1/delta/positions/margined"""
    client = get_client(_extract_subaccount(request))
    try:
        data = client.get_margined_positions(
            product_ids=product_ids,
            contract_types=contract_types,
        )
        return data
    except DeltaAPIError as err:
        return JSONResponse(status_code=err.status_code or 400, content=err.to_dict())
    except Exception as e:
        return JSONResponse(status_code=500, content={"success": False, "error": {"message": str(e)}})


@router.post("/positions/change-margin")
async def change_position_margin(request: Request):
    """POST /api/v1/delta/positions/change-margin"""
    client = get_client(_extract_subaccount(request))
    try:
        body = await request.json()
        product_id = int(body.get("product_id", 0))
        delta_margin = str(body.get("delta_margin", "0"))
        data = client.change_position_margin(product_id=product_id, delta_margin=delta_margin)
        logger.info("Changed margin on %s for product %s: %s", client.subaccount_name, product_id, delta_margin)
        return data
    except DeltaAPIError as err:
        return JSONResponse(status_code=err.status_code or 400, content=err.to_dict())
    except Exception as e:
        return JSONResponse(status_code=400, content={"success": False, "error": {"message": str(e)}})


@router.post("/positions/close-all")
async def close_all_positions(request: Request):
    """POST /api/v1/delta/positions/close-all"""
    client = get_client(_extract_subaccount(request))
    try:
        body = {}
        try:
            body = await request.json()
        except Exception:
            pass
        product_id = int(body["product_id"]) if "product_id" in body and body["product_id"] is not None else None
        data = client.close_all_positions(product_id=product_id)
        logger.warning("Emergency close-all executed on %s subaccount", client.subaccount_name)
        return data
    except DeltaAPIError as err:
        return JSONResponse(status_code=err.status_code or 400, content=err.to_dict())
    except Exception as e:
        return JSONResponse(status_code=400, content={"success": False, "error": {"message": str(e)}})


# =============================================================================
# 4. ORDERS LIFECYCLE (AUTHENTICATED)
# =============================================================================

@router.get("/orders")
async def get_active_orders(
    request: Request,
    product_ids: Optional[str] = Query(default=None),
    states: Optional[str] = Query(default="open,pending"),
    contract_types: Optional[str] = Query(default=None),
):
    """GET /api/v1/delta/orders"""
    client = get_client(_extract_subaccount(request))
    try:
        data = client.get_active_orders(
            product_ids=product_ids,
            states=states,
            contract_types=contract_types,
        )
        return data
    except DeltaAPIError as err:
        return JSONResponse(status_code=err.status_code or 400, content=err.to_dict())
    except Exception as e:
        return JSONResponse(status_code=500, content={"success": False, "error": {"message": str(e)}})


@router.post("/orders")
async def place_order(request: Request):
    """POST /api/v1/delta/orders"""
    client = get_client(_extract_subaccount(request))
    try:
        body = await request.json()
        size = int(body.get("size", 1))
        side = str(body.get("side", "buy")).lower()
        order_type = str(body.get("order_type", "market_order"))
        product_id = int(body["product_id"]) if "product_id" in body and body["product_id"] is not None else None
        product_symbol = body.get("product_symbol")
        limit_price = str(body["limit_price"]) if "limit_price" in body and body["limit_price"] is not None else None
        stop_price = str(body["stop_price"]) if "stop_price" in body and body["stop_price"] is not None else None
        client_order_id = body.get("client_order_id")

        bracket_sl = str(body.get("stop_loss_price") or body.get("bracket_stop_loss_price") or "") or None
        bracket_tp = str(body.get("take_profit_price") or body.get("bracket_take_profit_price") or "") or None

        data = client.place_order(
            size=size,
            side=side,
            order_type=order_type,
            product_id=product_id,
            product_symbol=product_symbol,
            limit_price=limit_price,
            stop_price=stop_price,
            bracket_stop_loss_price=bracket_sl,
            bracket_take_profit_price=bracket_tp,
            client_order_id=client_order_id,
        )
        logger.info(
            "Order placed on %s: %s %s contracts %s (TP=%s, SL=%s)",
            client.subaccount_name, side, size, product_symbol or product_id, bracket_tp, bracket_sl
        )
        return data
    except DeltaAPIError as err:
        return JSONResponse(status_code=err.status_code or 400, content=err.to_dict())
    except Exception as e:
        return JSONResponse(status_code=400, content={"success": False, "error": {"message": str(e)}})


@router.put("/orders")
async def edit_order(request: Request):
    """PUT /api/v1/delta/orders"""
    client = get_client(_extract_subaccount(request))
    try:
        body = await request.json()
        data = client.edit_order(
            order_id=int(body["id"]),
            product_id=int(body["product_id"]),
            limit_price=str(body["limit_price"]) if "limit_price" in body else None,
            size=int(body["size"]) if "size" in body else None,
        )
        return data
    except DeltaAPIError as err:
        return JSONResponse(status_code=err.status_code or 400, content=err.to_dict())
    except Exception as e:
        return JSONResponse(status_code=400, content={"success": False, "error": {"message": str(e)}})


@router.delete("/orders")
async def cancel_order(request: Request):
    """DELETE /api/v1/delta/orders"""
    client = get_client(_extract_subaccount(request))
    try:
        body: Dict[str, Any] = {}
        try:
            body = await request.json()
        except Exception:
            pass

        order_id = body.get("id") or body.get("order_id") or request.query_params.get("order_id")
        client_order_id = body.get("client_order_id") or request.query_params.get("client_order_id")
        product_id = body.get("product_id") or request.query_params.get("product_id")

        data = client.cancel_order(
            order_id=int(order_id) if order_id is not None else None,
            client_order_id=client_order_id,
            product_id=int(product_id) if product_id is not None else None,
        )
        logger.info("Cancelled order on %s: ID %s", client.subaccount_name, order_id or client_order_id)
        return data
    except DeltaAPIError as err:
        return JSONResponse(status_code=err.status_code or 400, content=err.to_dict())
    except Exception as e:
        return JSONResponse(status_code=400, content={"success": False, "error": {"message": str(e)}})


@router.delete("/orders/all")
async def cancel_all_orders(request: Request):
    """DELETE /api/v1/delta/orders/all"""
    client = get_client(_extract_subaccount(request))
    try:
        body = {}
        try:
            body = await request.json()
        except Exception:
            pass
        product_id = int(body["product_id"]) if "product_id" in body and body["product_id"] is not None else None
        contract_types = body.get("contract_types")
        data = client.cancel_all_orders(product_id=product_id, contract_types=contract_types)
        logger.warning("Cancelled all orders on %s", client.subaccount_name)
        return data
    except DeltaAPIError as err:
        return JSONResponse(status_code=err.status_code or 400, content=err.to_dict())
    except Exception as e:
        return JSONResponse(status_code=400, content={"success": False, "error": {"message": str(e)}})


@router.post("/orders/bracket")
async def place_bracket_order(request: Request):
    """POST /api/v1/delta/orders/bracket"""
    client = get_client(_extract_subaccount(request))
    try:
        body = await request.json()
        product_id = int(body["product_id"]) if "product_id" in body and body["product_id"] is not None else None
        product_symbol = body.get("product_symbol")
        stop_loss_price = str(body["stop_loss_price"]) if "stop_loss_price" in body and body["stop_loss_price"] is not None else None
        take_profit_price = str(body["take_profit_price"]) if "take_profit_price" in body and body["take_profit_price"] is not None else None

        data = client.place_bracket_order(
            product_id=product_id,
            product_symbol=product_symbol,
            stop_loss_price=stop_loss_price,
            take_profit_price=take_profit_price,
        )
        logger.info("Bracket TP/SL order placed on %s: TP=%s, SL=%s",
                    client.subaccount_name, take_profit_price, stop_loss_price)
        return data
    except DeltaAPIError as err:
        return JSONResponse(status_code=err.status_code or 400, content=err.to_dict())
    except Exception as e:
        return JSONResponse(status_code=400, content={"success": False, "error": {"message": str(e)}})


@router.post("/orders/leverage")
async def set_order_leverage(request: Request):
    """POST /api/v1/delta/orders/leverage"""
    client = get_client(_extract_subaccount(request))
    try:
        body = await request.json()
        product_id = int(body["product_id"])
        leverage = int(body["leverage"])
        data = client.set_order_leverage(product_id=product_id, leverage=leverage)
        logger.info("Set leverage on %s for product %s to %sx", client.subaccount_name, product_id, leverage)
        return data
    except DeltaAPIError as err:
        return JSONResponse(status_code=err.status_code or 400, content=err.to_dict())
    except Exception as e:
        return JSONResponse(status_code=400, content={"success": False, "error": {"message": str(e)}})


@router.get("/orders/leverage")
async def get_order_leverage(request: Request, product_id: int = Query(default=27)):
    """GET /api/v1/delta/orders/leverage"""
    client = get_client(_extract_subaccount(request))
    try:
        data = client.get_order_leverage(product_id=product_id)
        return data
    except DeltaAPIError as err:
        return JSONResponse(status_code=err.status_code or 400, content=err.to_dict())
    except Exception as e:
        return JSONResponse(status_code=500, content={"success": False, "error": {"message": str(e)}})


# =============================================================================
# 5. DEADMAN SWITCH / HEARTBEAT (AUTHENTICATED)
# =============================================================================

@router.post("/heartbeat/create")
async def create_heartbeat(request: Request):
    """POST /api/v1/delta/heartbeat/create"""
    client = get_client(_extract_subaccount(request))
    try:
        body = await request.json()
        heartbeat_id = str(body["heartbeat_id"])
        impact = body.get("impact", "contracts")
        unhealthy_count = int(body.get("unhealthy_count", 1))
        data = client.create_heartbeat(
            heartbeat_id=heartbeat_id,
            impact=impact,
            unhealthy_count=unhealthy_count,
        )
        logger.info("Registered heartbeat deadman switch: %s", heartbeat_id)
        return data
    except DeltaAPIError as err:
        return JSONResponse(status_code=err.status_code or 400, content=err.to_dict())
    except Exception as e:
        return JSONResponse(status_code=400, content={"success": False, "error": {"message": str(e)}})


@router.post("/heartbeat")
async def send_heartbeat(request: Request):
    """POST /api/v1/delta/heartbeat"""
    client = get_client(_extract_subaccount(request))
    try:
        body = await request.json()
        heartbeat_id = str(body["heartbeat_id"])
        ttl = int(body.get("ttl", 30000))
        data = client.send_heartbeat(heartbeat_id=heartbeat_id, ttl=ttl)
        return data
    except DeltaAPIError as err:
        return JSONResponse(status_code=err.status_code or 400, content=err.to_dict())
    except Exception as e:
        return JSONResponse(status_code=400, content={"success": False, "error": {"message": str(e)}})


@router.get("/heartbeat")
async def get_heartbeats(
    request: Request,
    user_id: Optional[int] = Query(default=None),
    heartbeat_id: Optional[str] = Query(default=None),
):
    """GET /api/v1/delta/heartbeat"""
    client = get_client(_extract_subaccount(request))
    try:
        data = client.get_heartbeats(user_id=user_id, heartbeat_id=heartbeat_id)
        return data
    except DeltaAPIError as err:
        return JSONResponse(status_code=err.status_code or 400, content=err.to_dict())
    except Exception as e:
        return JSONResponse(status_code=500, content={"success": False, "error": {"message": str(e)}})
