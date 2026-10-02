"""Unit and Integration tests for Indicator Engine (Phase 5).

Tests:
1. EMA 9 & EMA 20 calculations, trend detection, golden/death cross.
2. MACD (12, 26, 9) signal line, histogram momentum, and crossover.
3. VWAP and 1-sigma / 2-sigma standard deviation bands.
4. Pivot Points (Standard, Fibonacci, Camarilla).
5. Support & Resistance swing fractals and zone clustering.
6. REST API routes (/indicators/{symbol}, /ema, /macd, /vwap, /pivots, /support-resistance, /calculate).
"""

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.db.session import init_db
from app.services.indicator_engine import (
    analyze_ema,
    analyze_macd,
    analyze_pivots,
    analyze_support_resistance,
    analyze_vwap,
    calculate_ema_series,
    compute_composite_score,
    indicator_engine,
)
from app.services.market_data_engine import market_engine


def test_calculate_ema_series():
    """Verify EMA mathematical formula and smoothing behavior."""
    data = [10.0, 10.0, 10.0, 10.0, 10.0]
    ema = calculate_ema_series(data, period=3)
    assert len(ema) == 5
    for val in ema:
        assert pytest.approx(val, 0.001) == 10.0

    # Ascending series
    prices = [10.0, 20.0, 30.0, 40.0, 50.0, 60.0]
    ema_3 = calculate_ema_series(prices, period=3)
    assert len(ema_3) == len(prices)
    assert ema_3[-1] > ema_3[0]


def test_ema_crossover_and_trend():
    """Verify Golden Cross and Death Cross state detection."""
    # Bullish trend (increasing series)
    bullish_prices = [100.0 + (i * 2.0) for i in range(25)]
    res = analyze_ema(bullish_prices, fast_period=9, slow_period=20)
    assert res.ema_fast is not None
    assert res.ema_slow is not None
    assert res.ema_fast > res.ema_slow
    assert res.price_position == "ABOVE_BOTH"
    assert res.trend in ("BULLISH_TREND", "BULLISH_CROSS")

    # Bearish trend (decreasing series)
    bearish_prices = [200.0 - (i * 2.0) for i in range(25)]
    res_b = analyze_ema(bearish_prices, fast_period=9, slow_period=20)
    assert res_b.ema_fast < res_b.ema_slow
    assert res_b.price_position == "BELOW_BOTH"
    assert res_b.trend in ("BEARISH_TREND", "BEARISH_CROSS")


def test_macd_calculation_and_momentum():
    """Verify MACD Line, Signal Line, and Histogram momentum state."""
    prices = [50.0 + (i * 1.5) for i in range(35)]
    macd_res = analyze_macd(prices, fast_period=12, slow_period=26, signal_period=9)
    assert macd_res.macd_line is not None
    assert macd_res.signal_line is not None
    assert macd_res.histogram is not None
    assert macd_res.zero_line_position == "ABOVE_ZERO"
    assert "BULLISH" in macd_res.momentum_state


def test_vwap_and_standard_deviation_bands():
    """Verify VWAP typical price weighting and 1-sigma/2-sigma bands."""
    highs = [105.0, 107.0, 110.0, 112.0, 115.0]
    lows = [95.0, 97.0, 100.0, 102.0, 105.0]
    closes = [100.0, 102.0, 105.0, 108.0, 110.0]
    volumes = [100.0, 200.0, 150.0, 300.0, 250.0]

    vwap_res = analyze_vwap(highs, lows, closes, volumes)
    assert vwap_res.vwap is not None
    assert vwap_res.upper_band_1 > vwap_res.vwap
    assert vwap_res.upper_band_2 > vwap_res.upper_band_1
    assert vwap_res.lower_band_1 < vwap_res.vwap
    assert vwap_res.lower_band_2 < vwap_res.lower_band_1
    assert vwap_res.bias in (
        "ABOVE_VWAP",
        "EXTENDED_BULLISH",
        "OVERBOUGHT",
        "AT_VWAP",
    )


def test_pivot_points_three_models():
    """Verify Standard, Fibonacci, and Camarilla pivot formulas."""
    h = 82000.0
    l = 80000.0
    c = 81500.0
    highs = [h]
    lows = [l]
    closes = [c]

    pivots = analyze_pivots(highs, lows, closes, current_price=c)
    # Standard Pivot P = (H + L + C) / 3 = (82000 + 80000 + 81500) / 3 = 81166.67
    assert pytest.approx(pivots.standard.pivot, 1.0) == 81166.67
    assert pivots.standard.r1 > pivots.standard.pivot
    assert pivots.standard.s1 < pivots.standard.pivot

    # Fibonacci Pivot
    assert pivots.fibonacci.r1 > pivots.fibonacci.pivot
    assert pivots.fibonacci.s1 < pivots.fibonacci.pivot

    # Camarilla Pivot
    assert pivots.camarilla.r3 > pivots.camarilla.r1
    assert pivots.camarilla.s3 < pivots.camarilla.s1


def test_support_resistance_fractal_swings():
    """Verify swing highs/lows detection and nearest support/resistance distance."""
    # Pattern with a clear peak and a clear trough
    highs = [10, 12, 15, 18, 25, 20, 18, 15, 16, 17, 18, 19, 14, 12, 10]
    lows = [8, 10, 12, 15, 20, 16, 14, 10, 5, 12, 14, 15, 10, 8, 7]
    closes = [9, 11, 14, 17, 22, 18, 16, 12, 8, 15, 16, 17, 12, 10, 9]

    sr = analyze_support_resistance(highs, lows, closes, current_price=16.0, window=2)
    assert sr.nearest_support is not None
    assert sr.nearest_resistance is not None
    assert sr.nearest_support <= 16.0
    assert sr.nearest_resistance >= 16.0
    assert sr.support_distance_pct >= 0.0
    assert sr.resistance_distance_pct >= 0.0


def test_composite_technical_score():
    """Verify technical scoring logic bounds and bias categorization."""
    prices = [100.0 + (i * 2.0) for i in range(30)]
    highs = [p + 2.0 for p in prices]
    lows = [p - 2.0 for p in prices]
    volumes = [100.0 for _ in prices]

    ema = analyze_ema(prices)
    macd = analyze_macd(prices)
    vwap = analyze_vwap(highs, lows, prices, volumes)
    pivots = analyze_pivots(highs, lows, prices, prices[-1])
    sr = analyze_support_resistance(highs, lows, prices, prices[-1])

    score, bias, reasons = compute_composite_score(
        ema, macd, vwap, pivots, sr, prices[-1]
    )
    assert -100 <= score <= 100
    assert bias in (
        "STRONG_BULLISH",
        "BULLISH",
        "NEUTRAL",
        "BEARISH",
        "STRONG_BEARISH",
    )
    assert len(reasons) > 0


@pytest.mark.asyncio
async def test_indicator_api_endpoints():
    """Verify FastAPI routes for indicators."""
    await init_db()

    # Prepopulate BTCUSD 5m buffer
    buf = market_engine.get_buffer("BTCUSD", "5m")
    base_p = 80000.0
    for i in range(30):
        p = base_p + (i * 25.0)
        buf.upsert(
            open_time=1700000000 + (i * 300),
            open_p=p - 10,
            high_p=p + 35,
            low_p=p - 25,
            close_p=p,
            volume=50.0 + i,
        )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # 1. Full snapshot
        res = await ac.get("/api/v1/indicators/BTCUSD?timeframe=5m")
        assert res.status_code == 200
        data = res.json()
        assert data["symbol"] == "BTCUSD"
        assert "ema" in data
        assert "macd" in data
        assert "vwap" in data
        assert "pivots" in data
        assert "support_resistance" in data
        assert "technical_score" in data
        assert "technical_bias" in data

        # 2. Individual component endpoints
        ema_res = await ac.get("/api/v1/indicators/BTCUSD/ema")
        assert ema_res.status_code == 200
        assert ema_res.json()["ema_fast"] is not None

        macd_res = await ac.get("/api/v1/indicators/BTCUSD/macd")
        assert macd_res.status_code == 200
        assert macd_res.json()["macd_line"] is not None

        vwap_res = await ac.get("/api/v1/indicators/BTCUSD/vwap")
        assert vwap_res.status_code == 200
        assert vwap_res.json()["vwap"] is not None

        pivots_res = await ac.get("/api/v1/indicators/BTCUSD/pivots")
        assert pivots_res.status_code == 200
        assert "standard" in pivots_res.json()

        sr_res = await ac.get("/api/v1/indicators/BTCUSD/support-resistance")
        assert sr_res.status_code == 200
        assert sr_res.json()["nearest_support"] is not None

        # 3. Multi-timeframe
        mtf_res = await ac.get("/api/v1/indicators/BTCUSD?timeframe=all")
        assert mtf_res.status_code == 200
        assert "timeframes" in mtf_res.json()

        # 4. Custom calculate
        calc_res = await ac.post(
            "/api/v1/indicators/calculate",
            json={
                "symbol": "CUSTOM_BTC",
                "timeframe": "15m",
                "opens": [10, 11, 12, 13, 14, 15, 16, 17, 18, 19],
                "highs": [12, 13, 14, 15, 16, 17, 18, 19, 20, 21],
                "lows": [9, 10, 11, 12, 13, 14, 15, 16, 17, 18],
                "closes": [11, 12, 13, 14, 15, 16, 17, 18, 19, 20],
                "volumes": [100, 100, 100, 100, 100, 100, 100, 100, 100, 100],
            },
        )
        assert calc_res.status_code == 200
        assert calc_res.json()["symbol"] == "CUSTOM_BTC"
        assert calc_res.json()["technical_score"] is not None
