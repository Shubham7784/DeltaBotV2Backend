"""Unit tests for Delta API endpoints mounted in FastAPI."""

import pytest
from httpx import AsyncClient, ASGITransport
from app.main import app


@pytest.mark.asyncio
async def test_tickers_endpoint():
    """Verify that /api/v1/delta/tickers returns a successful payload."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.get("/api/v1/delta/tickers/BTCUSD")
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert "result" in data


@pytest.mark.asyncio
async def test_wallet_balances_endpoint():
    """Verify wallet balances return simulated paper balances when keys are absent."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.get("/api/v1/delta/wallet/balances")
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert isinstance(data["result"], list)


@pytest.mark.asyncio
async def test_order_placement_and_cancel_in_fastapi():
    """Verify placing and canceling a paper order through FastAPI."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # Place order
        order_payload = {
            "product_id": 27,
            "product_symbol": "BTCUSD",
            "size": 1,
            "side": "buy",
            "order_type": "limit_order",
            "limit_price": "60000",
        }
        res = await ac.post("/api/v1/delta/orders", json=order_payload)
        assert res.status_code == 200
        order_data = res.json()
        assert order_data["success"] is True
        order_id = order_data["result"]["id"]

        # Cancel order
        del_res = await ac.request("DELETE", "/api/v1/delta/orders", json={"id": order_id})
        assert del_res.status_code == 200
        del_data = del_res.json()
        assert del_data["success"] is True


@pytest.mark.asyncio
async def test_positions_endpoint():
    """Verify that /api/v1/delta/positions returns positions for both subaccounts."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # Test futures subaccount default margined
        res_fut = await ac.get("/api/v1/delta/positions?subaccount=futures")
        assert res_fut.status_code == 200
        fut_data = res_fut.json()
        assert fut_data["success"] is True
        assert isinstance(fut_data["result"], list)

        # Test futures with margined=false
        res_unmargined = await ac.get("/api/v1/delta/positions?subaccount=futures&margined=false")
        assert res_unmargined.status_code == 200
        assert res_unmargined.json()["success"] is True

        # Test options subaccount
        res_opt = await ac.get("/api/v1/delta/positions?subaccount=options")
        assert res_opt.status_code == 200
        assert res_opt.json()["success"] is True
