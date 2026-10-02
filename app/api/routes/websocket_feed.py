"""Compatibility endpoints for the frontend real-time market feed."""

import asyncio
from datetime import datetime, timezone
from typing import Any, Dict, Set

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.services.market_data_engine import market_engine

router = APIRouter(tags=["WebSocket Feed"])
_clients: Set[WebSocket] = set()
_feed_connected = False


def get_feed_status() -> Dict[str, Any]:
    """Return the frontend WebSocket feed status contract."""
    market_status = market_engine.get_status()
    return {
        "status": "connected" if _feed_connected else "disconnected",
        "endpoint": "backend market data cache",
        "subscribed_channels": ["prices", "candles"],
        "messages_received": 0,
        "messages_per_sec": 0,
        "last_heartbeat": datetime.now(timezone.utc).isoformat(),
        "uptime_seconds": 0,
        "connected_clients": len(_clients),
        "latency_ms": 0,
        "reconnect_count": 0,
        "poller_active": market_status.poller_active,
    }


@router.get("/ws/status")
async def websocket_status() -> Dict[str, Any]:
    return get_feed_status()


@router.post("/ws/connect")
async def connect_feed() -> Dict[str, Any]:
    global _feed_connected
    _feed_connected = True
    return {"success": True, "stats": get_feed_status()}


@router.post("/ws/disconnect")
async def disconnect_feed() -> Dict[str, Any]:
    global _feed_connected
    _feed_connected = False
    return {"success": True, "stats": get_feed_status()}


@router.get("/ws/orderbook/{symbol}")
async def orderbook_snapshot(symbol: str) -> Dict[str, Any]:
    sym = symbol.upper()
    live = market_engine.get_live_price(sym) or {}
    mark_price = float(live.get("mark_price") or 0.0)
    return {
        "symbol": sym,
        "bids": [],
        "asks": [],
        "timestamp": int(datetime.now(timezone.utc).timestamp() * 1000),
        "spread": 0,
        "mid_price": mark_price,
    }


@router.websocket("/ws")
async def frontend_websocket(websocket: WebSocket) -> None:
    global _feed_connected
    await websocket.accept()
    _clients.add(websocket)
    _feed_connected = True
    try:
        await websocket.send_json(
            {
                "type": "welcome",
                "ws_status": get_feed_status(),
                "live_prices": market_engine.get_status().live_prices,
            }
        )
        while True:
            await asyncio.sleep(15)
            await websocket.send_json({"type": "ws_status", "data": get_feed_status()})
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        _clients.discard(websocket)
        if not _clients:
            _feed_connected = False
