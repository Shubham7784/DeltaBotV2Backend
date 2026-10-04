"""Indicator Engine (Phase 5) for Delta Bot V2.

Computes technical indicators for crypto futures and options:
1. EMA 9 & EMA 20 (Exponential Moving Averages, crossovers, trend alignment, slopes)
2. MACD (12, 26, 9 line, signal line, histogram momentum & crossovers)
3. VWAP (Volume Weighted Average Price, 1-sigma & 2-sigma standard deviation bands)
4. Pivot Points (Standard/Floor, Fibonacci, Camarilla intraday support/resistance)
5. Support & Resistance (Fractal swing detection, zone clustering, proximity tests)
6. Composite Technical Score (-100 to +100) and Technical Bias synthesis.
"""

import logging
import math
from typing import Any, Dict, List, Optional, Tuple

from app.schemas.indicator import (
    CustomCalculateRequest,
    EMAResult,
    IndicatorSnapshot,
    MACDResult,
    MultiTimeframeSnapshot,
    PivotLevels,
    PivotPointsResult,
    SRZone,
    SupportResistanceResult,
    VWAPResult,
)
from app.services.market_data_engine import CandleItem, market_engine

logger = logging.getLogger("delta_bot.indicator_engine")


def calculate_ema_series(values: List[float], period: int) -> List[float]:
    """Calculates Exponential Moving Average series using k = 2 / (period + 1).

    If the input list has fewer elements than the period, rolling SMA is used as fallback.
    """
    if not values:
        return []

    n = len(values)
    k = 2.0 / (period + 1.0)
    ema_series: List[float] = []

    if n < period:
        # Fallback to cumulative rolling average
        cum = 0.0
        for i, val in enumerate(values):
            cum += val
            ema_series.append(cum / (i + 1))
        return ema_series

    # Seed with Simple Moving Average of first 'period' elements
    initial_sma = sum(values[:period]) / period
    ema_series = [initial_sma] * period
    current_ema = initial_sma

    for val in values[period:]:
        current_ema = (val * k) + (current_ema * (1.0 - k))
        ema_series.append(current_ema)

    return ema_series


def analyze_ema(
    closes: List[float],
    fast_period: int = 9,
    slow_period: int = 20,
) -> EMAResult:
    """Computes EMA 9 and EMA 20, detects Golden/Death crossovers, and gauges trend."""
    if not closes:
        return EMAResult(fast_period=fast_period, slow_period=slow_period)

    current_price = closes[-1]
    fast_series = calculate_ema_series(closes, fast_period)
    slow_series = calculate_ema_series(closes, slow_period)

    ema_fast = fast_series[-1] if fast_series else current_price
    ema_slow = slow_series[-1] if slow_series else current_price

    # Measure slope over last 3 bars
    fast_slope = 0.0
    slow_slope = 0.0
    if len(fast_series) >= 4 and fast_series[-4] > 0:
        fast_slope = ((ema_fast - fast_series[-4]) / fast_series[-4]) * 100.0
    if len(slow_series) >= 4 and slow_series[-4] > 0:
        slow_slope = ((ema_slow - slow_series[-4]) / slow_series[-4]) * 100.0

    # Spread percentage
    spread_pct = ((ema_fast - ema_slow) / ema_slow * 100.0) if ema_slow > 0 else 0.0

    # Crossover detection
    trend = "NEUTRAL"
    if len(fast_series) >= 2 and len(slow_series) >= 2:
        prev_fast = fast_series[-2]
        prev_slow = slow_series[-2]

        if prev_fast <= prev_slow and ema_fast > ema_slow:
            trend = "BULLISH_CROSS"
        elif prev_fast >= prev_slow and ema_fast < ema_slow:
            trend = "BEARISH_CROSS"
        elif ema_fast > ema_slow:
            trend = "BULLISH_TREND"
        elif ema_fast < ema_slow:
            trend = "BEARISH_TREND"
    else:
        trend = "BULLISH_TREND" if ema_fast > ema_slow else "BEARISH_TREND"

    # Price position
    price_pos = "BETWEEN"
    if current_price > ema_fast and current_price > ema_slow:
        price_pos = "ABOVE_BOTH"
    elif current_price < ema_fast and current_price < ema_slow:
        price_pos = "BELOW_BOTH"

    return EMAResult(
        ema_fast=round(ema_fast, 4),
        ema_slow=round(ema_slow, 4),
        fast_period=fast_period,
        slow_period=slow_period,
        trend=trend,
        price_position=price_pos,
        spread_pct=round(spread_pct, 4),
        fast_slope=round(fast_slope, 4),
        slow_slope=round(slow_slope, 4),
    )


def analyze_macd(
    closes: List[float],
    fast_period: int = 12,
    slow_period: int = 26,
    signal_period: int = 9,
) -> MACDResult:
    """Computes MACD (12, 26, 9), detecting signal crossovers and momentum expansion."""
    if not closes:
        return MACDResult(
            fast_period=fast_period,
            slow_period=slow_period,
            signal_period=signal_period,
        )

    fast_ema = calculate_ema_series(closes, fast_period)
    slow_ema = calculate_ema_series(closes, slow_period)

    # MACD Line = Fast EMA - Slow EMA
    min_len = min(len(fast_ema), len(slow_ema))
    macd_line_series = [fast_ema[i] - slow_ema[i] for i in range(min_len)]

    if not macd_line_series:
        return MACDResult(
            fast_period=fast_period,
            slow_period=slow_period,
            signal_period=signal_period,
        )

    # Signal Line = EMA 9 of MACD Line
    signal_series = calculate_ema_series(macd_line_series, signal_period)

    min_sig_len = min(len(macd_line_series), len(signal_series))
    histogram_series = [macd_line_series[i] - signal_series[i] for i in range(min_sig_len)]

    curr_macd = macd_line_series[-1]
    curr_signal = signal_series[-1]
    curr_hist = histogram_series[-1]

    # Crossover detection
    crossover = "NONE"
    if len(macd_line_series) >= 2 and len(signal_series) >= 2:
        prev_macd = macd_line_series[-2]
        prev_sig = signal_series[-2]
        if prev_macd <= prev_sig and curr_macd > curr_signal:
            crossover = "BULLISH_CROSS"
        elif prev_macd >= prev_sig and curr_macd < curr_signal:
            crossover = "BEARISH_CROSS"

    # Zero line position
    zero_pos = "AT_ZERO"
    if curr_macd > 0.001:
        zero_pos = "ABOVE_ZERO"
    elif curr_macd < -0.001:
        zero_pos = "BELOW_ZERO"

    # Momentum state
    mom_state = "NEUTRAL"
    if len(histogram_series) >= 2:
        prev_hist = histogram_series[-2]
        if curr_hist >= 0:
            mom_state = "BULLISH_EXPANDING" if curr_hist >= prev_hist else "BULLISH_CONTRACTING"
        else:
            mom_state = "BEARISH_EXPANDING" if curr_hist <= prev_hist else "BEARISH_CONTRACTING"

    return MACDResult(
        macd_line=round(curr_macd, 4),
        signal_line=round(curr_signal, 4),
        histogram=round(curr_hist, 4),
        fast_period=fast_period,
        slow_period=slow_period,
        signal_period=signal_period,
        crossover=crossover,
        zero_line_position=zero_pos,
        momentum_state=mom_state,
    )


def analyze_vwap(
    highs: List[float],
    lows: List[float],
    closes: List[float],
    volumes: List[float],
) -> VWAPResult:
    """Computes VWAP and standard deviation bands (1-sigma, 2-sigma)."""
    n = min(len(highs), len(lows), len(closes), len(volumes))
    if n == 0:
        return VWAPResult()

    current_price = closes[-1]
    cum_vol = 0.0
    cum_tp_vol = 0.0
    typical_prices: List[float] = []

    for i in range(n):
        tp = (highs[i] + lows[i] + closes[i]) / 3.0
        typical_prices.append(tp)
        v = max(volumes[i], 0.0001)  # avoid zero vol division
        cum_vol += v
        cum_tp_vol += tp * v

    vwap = cum_tp_vol / cum_vol if cum_vol > 0 else current_price

    # Compute variance weighted by volume
    weighted_variance_sum = 0.0
    for i in range(n):
        v = max(volumes[i], 0.0001)
        weighted_variance_sum += v * ((typical_prices[i] - vwap) ** 2)

    variance = weighted_variance_sum / cum_vol if cum_vol > 0 else 0.0
    std_dev = math.sqrt(max(variance, 0.0))

    ub1 = vwap + (1.0 * std_dev)
    ub2 = vwap + (2.0 * std_dev)
    lb1 = vwap - (1.0 * std_dev)
    lb2 = vwap - (2.0 * std_dev)

    dist_pct = ((current_price - vwap) / vwap * 100.0) if vwap > 0 else 0.0

    # Categorize VWAP bias
    if current_price > ub2:
        bias = "OVERBOUGHT"
    elif current_price > ub1:
        bias = "EXTENDED_BULLISH"
    elif current_price > vwap + (0.0005 * vwap):
        bias = "ABOVE_VWAP"
    elif current_price < lb2:
        bias = "OVERSOLD"
    elif current_price < lb1:
        bias = "EXTENDED_BEARISH"
    elif current_price < vwap - (0.0005 * vwap):
        bias = "BELOW_VWAP"
    else:
        bias = "AT_VWAP"

    return VWAPResult(
        vwap=round(vwap, 4),
        upper_band_1=round(ub1, 4),
        upper_band_2=round(ub2, 4),
        lower_band_1=round(lb1, 4),
        lower_band_2=round(lb2, 4),
        std_dev=round(std_dev, 4),
        distance_pct=round(dist_pct, 4),
        bias=bias,
    )


def analyze_pivots(
    highs: List[float],
    lows: List[float],
    closes: List[float],
    current_price: float,
) -> PivotPointsResult:
    """Calculates Standard Floor, Fibonacci, and Camarilla Pivot Points."""
    if not highs or not lows or not closes:
        zero_levels = PivotLevels(pivot=0, r1=0, s1=0, r2=0, s2=0, r3=0, s3=0)
        return PivotPointsResult(standard=zero_levels, fibonacci=zero_levels, camarilla=zero_levels)

    # Reference High, Low, Close over the lookback window (or last 24 bars)
    lookback = min(len(highs), 24)
    h = max(highs[-lookback:])
    l = min(lows[-lookback:])
    c = closes[-1]
    rng = max(h - l, 0.001)

    # 1. Standard Floor Pivots
    p_std = (h + l + c) / 3.0
    r1_std = (2.0 * p_std) - l
    s1_std = (2.0 * p_std) - h
    r2_std = p_std + rng
    s2_std = p_std - rng
    r3_std = h + (2.0 * (p_std - l))
    s3_std = l - (2.0 * (h - p_std))

    std_levels = PivotLevels(
        pivot=round(p_std, 2),
        r1=round(r1_std, 2),
        s1=round(s1_std, 2),
        r2=round(r2_std, 2),
        s2=round(s2_std, 2),
        r3=round(r3_std, 2),
        s3=round(s3_std, 2),
    )

    # 2. Fibonacci Pivots
    p_fib = (h + l + c) / 3.0
    r1_fib = p_fib + (0.382 * rng)
    s1_fib = p_fib - (0.382 * rng)
    r2_fib = p_fib + (0.618 * rng)
    s2_fib = p_fib - (0.618 * rng)
    r3_fib = p_fib + (1.000 * rng)
    s3_fib = p_fib - (1.000 * rng)

    fib_levels = PivotLevels(
        pivot=round(p_fib, 2),
        r1=round(r1_fib, 2),
        s1=round(s1_fib, 2),
        r2=round(r2_fib, 2),
        s2=round(s2_fib, 2),
        r3=round(r3_fib, 2),
        s3=round(s3_fib, 2),
    )

    # 3. Camarilla Pivots
    r4_cam = c + (rng * 1.1 / 2.0)
    r3_cam = c + (rng * 1.1 / 4.0)
    r2_cam = c + (rng * 1.1 / 6.0)
    r1_cam = c + (rng * 1.1 / 12.0)
    s1_cam = c - (rng * 1.1 / 12.0)
    s2_cam = c - (rng * 1.1 / 6.0)
    s3_cam = c - (rng * 1.1 / 4.0)
    s4_cam = c - (rng * 1.1 / 2.0)

    cam_levels = PivotLevels(
        pivot=round(c, 2),
        r1=round(r1_cam, 2),
        s1=round(s1_cam, 2),
        r2=round(r2_cam, 2),
        s2=round(s2_cam, 2),
        r3=round(r3_cam, 2),
        s3=round(s3_cam, 2),
        r4=round(r4_cam, 2),
        s4=round(s4_cam, 2),
    )

    # Find nearest support and resistance across the standard levels
    all_levels = [
        std_levels.r3,
        std_levels.r2,
        std_levels.r1,
        std_levels.pivot,
        std_levels.s1,
        std_levels.s2,
        std_levels.s3,
    ]
    supports = [lvl for lvl in all_levels if lvl < current_price]
    resistances = [lvl for lvl in all_levels if lvl > current_price]

    nearest_sup = max(supports) if supports else None
    nearest_res = min(resistances) if resistances else None

    sup_dist_pct = (
        round(((current_price - nearest_sup) / current_price * 100.0), 3)
        if nearest_sup
        else None
    )
    res_dist_pct = (
        round(((nearest_res - current_price) / current_price * 100.0), 3)
        if nearest_res
        else None
    )

    return PivotPointsResult(
        standard=std_levels,
        fibonacci=fib_levels,
        camarilla=cam_levels,
        nearest_support=nearest_sup,
        nearest_resistance=nearest_res,
        support_distance_pct=sup_dist_pct,
        resistance_distance_pct=res_dist_pct,
    )


def analyze_support_resistance(
    highs: List[float],
    lows: List[float],
    closes: List[float],
    current_price: float,
    window: int = 2,
) -> SupportResistanceResult:
    """Detects fractal swing highs and lows, clusters them into S/R zones, and checks proximity."""
    n = min(len(highs), len(lows), len(closes))
    if n < (window * 2) + 1:
        return SupportResistanceResult(
            nearest_support=round(current_price * 0.98, 2),
            nearest_resistance=round(current_price * 1.02, 2),
            support_distance_pct=2.0,
            resistance_distance_pct=2.0,
            proximity_status="IN_RANGE",
        )

    swing_highs: List[float] = []
    swing_lows: List[float] = []

    for i in range(window, n - window):
        # Swing High
        is_high = True
        for w in range(1, window + 1):
            if highs[i] <= highs[i - w] or highs[i] <= highs[i + w]:
                is_high = False
                break
        if is_high:
            swing_highs.append(highs[i])

        # Swing Low
        is_low = True
        for w in range(1, window + 1):
            if lows[i] >= lows[i - w] or lows[i] >= lows[i + w]:
                is_low = False
                break
        if is_low:
            swing_lows.append(lows[i])

    # Cluster swing points into zones (tolerance 0.35%)
    zones: List[SRZone] = []

    def cluster_points(points: List[float], level_type: str):
        if not points:
            return
        sorted_pts = sorted(points)
        clusters: List[List[float]] = []
        for p in sorted_pts:
            if not clusters:
                clusters.append([p])
            else:
                last_cluster = clusters[-1]
                avg = sum(last_cluster) / len(last_cluster)
                if abs(p - avg) / avg <= 0.0035:
                    last_cluster.append(p)
                else:
                    clusters.append([p])

        for c in clusters:
            avg_price = round(sum(c) / len(c), 2)
            touches = len(c)
            strength = min(round(0.3 + (touches * 0.2), 2), 1.0)
            zones.append(
                SRZone(
                    price=avg_price,
                    level_type=level_type,
                    touches=touches,
                    strength=strength,
                )
            )

    cluster_points(swing_lows, "SUPPORT")
    cluster_points(swing_highs, "RESISTANCE")

    # Filter supports and resistances relative to current price
    supports = [z.price for z in zones if z.price < current_price]
    resistances = [z.price for z in zones if z.price > current_price]

    nearest_sup = max(supports) if supports else round(min(lows), 2)
    nearest_res = min(resistances) if resistances else round(max(highs), 2)

    sup_dist_pct = round(((current_price - nearest_sup) / current_price * 100.0), 3)
    res_dist_pct = round(((nearest_res - current_price) / current_price * 100.0), 3)

    proximity_status = "IN_RANGE"
    if sup_dist_pct <= 0.25:
        proximity_status = "TESTING_SUPPORT"
    elif res_dist_pct <= 0.25:
        proximity_status = "TESTING_RESISTANCE"
    elif current_price > max(highs[-5:]):
        proximity_status = "BREAKOUT_ABOVE"
    elif current_price < min(lows[-5:]):
        proximity_status = "BREAKDOWN_BELOW"

    return SupportResistanceResult(
        nearest_support=nearest_sup,
        nearest_resistance=nearest_res,
        support_distance_pct=sup_dist_pct,
        resistance_distance_pct=res_dist_pct,
        zones=sorted(zones, key=lambda z: z.price, reverse=True),
        proximity_status=proximity_status,
    )


def compute_composite_score(
    ema: EMAResult,
    macd: MACDResult,
    vwap: VWAPResult,
    pivots: PivotPointsResult,
    sr: SupportResistanceResult,
    current_price: float,
) -> Tuple[int, str, List[str]]:
    """Synthesizes indicator readings into a composite score from -100 to +100 and bias."""
    score = 0
    reasons: List[str] = []

    # 1. EMA 9/20 Factor (Up to ±30 points)
    if ema.trend == "BULLISH_CROSS":
        score += 30
        reasons.append("EMA 9 crossed above EMA 20 (Bullish Golden Cross)")
    elif ema.trend == "BEARISH_CROSS":
        score -= 30
        reasons.append("EMA 9 crossed below EMA 20 (Bearish Death Cross)")
    elif ema.trend == "BULLISH_TREND":
        score += 20
        reasons.append("EMA 9 above EMA 20 in bullish alignment")
    elif ema.trend == "BEARISH_TREND":
        score -= 20
        reasons.append("EMA 9 below EMA 20 in bearish alignment")

    if ema.price_position == "ABOVE_BOTH":
        score += 10
    elif ema.price_position == "BELOW_BOTH":
        score -= 10

    # 2. MACD Factor (Up to ±25 points)
    if macd.crossover == "BULLISH_CROSS":
        score += 25
        reasons.append("MACD line crossed above signal line")
    elif macd.crossover == "BEARISH_CROSS":
        score -= 25
        reasons.append("MACD line crossed below signal line")
    elif macd.momentum_state == "BULLISH_EXPANDING":
        score += 15
        reasons.append("MACD histogram positive and expanding")
    elif macd.momentum_state == "BEARISH_EXPANDING":
        score -= 15
        reasons.append("MACD histogram negative and expanding")
    elif macd.zero_line_position == "ABOVE_ZERO":
        score += 5
    elif macd.zero_line_position == "BELOW_ZERO":
        score -= 5

    # 3. VWAP Factor (Up to ±20 points)
    if vwap.bias in ("OVERBOUGHT", "EXTENDED_BULLISH"):
        score += 15
        reasons.append(f"Price trending strongly above VWAP (+{vwap.distance_pct}%)")
    elif vwap.bias == "ABOVE_VWAP":
        score += 10
        reasons.append(f"Price trading above VWAP (+{vwap.distance_pct}%)")
    elif vwap.bias in ("OVERSOLD", "EXTENDED_BEARISH"):
        score -= 15
        reasons.append(f"Price trading strongly below VWAP ({vwap.distance_pct}%)")
    elif vwap.bias == "BELOW_VWAP":
        score -= 10
        reasons.append(f"Price trading below VWAP ({vwap.distance_pct}%)")

    # 4. Support / Resistance & Pivots (Up to ±15 points)
    if sr.proximity_status == "TESTING_SUPPORT":
        score += 15
        reasons.append(f"Testing primary support at ${sr.nearest_support} (potential bounce zone)")
    elif sr.proximity_status == "TESTING_RESISTANCE":
        score -= 15
        reasons.append(f"Testing primary resistance at ${sr.nearest_resistance} (potential rejection zone)")
    elif sr.proximity_status == "BREAKOUT_ABOVE":
        score += 20
        reasons.append(f"Breakout above local swing resistance")
    elif sr.proximity_status == "BREAKDOWN_BELOW":
        score -= 20
        reasons.append(f"Breakdown below local swing support")

    # Bound to [-100, 100]
    final_score = max(min(score, 100), -100)

    # Determine qualitative bias
    if final_score >= 45:
        bias = "STRONG_BULLISH"
    elif final_score >= 15:
        bias = "BULLISH"
    elif final_score <= -45:
        bias = "STRONG_BEARISH"
    elif final_score <= -15:
        bias = "BEARISH"
    else:
        bias = "NEUTRAL"

    if not reasons:
        reasons.append("Technical indicators currently within neutral range")

    return final_score, bias, reasons


class IndicatorEngine:
    """Singleton service managing technical indicators across symbols and timeframes."""

    def calculate_from_series(
        self,
        symbol: str,
        timeframe: str,
        opens: List[float],
        highs: List[float],
        lows: List[float],
        closes: List[float],
        volumes: List[float],
        timestamps: Optional[List[int]] = None,
    ) -> IndicatorSnapshot:
        """Computes all technical indicators on supplied raw OHLCV arrays."""
        n = min(len(opens), len(highs), len(lows), len(closes), len(volumes))
        if n == 0:
            raise ValueError("Cannot calculate indicators on empty price arrays")

        curr_price = closes[-1]
        last_time = timestamps[-1] if (timestamps and len(timestamps) > 0) else 0

        # 1. EMA 9 & EMA 20
        ema_result = analyze_ema(closes=closes, fast_period=9, slow_period=20)

        # 2. MACD (12, 26, 9)
        macd_result = analyze_macd(closes=closes, fast_period=12, slow_period=26, signal_period=9)

        # 3. VWAP
        vwap_result = analyze_vwap(highs=highs, lows=lows, closes=closes, volumes=volumes)

        # 4. Pivot Points
        pivots_result = analyze_pivots(
            highs=highs, lows=lows, closes=closes, current_price=curr_price
        )

        # 5. Support & Resistance
        sr_result = analyze_support_resistance(
            highs=highs, lows=lows, closes=closes, current_price=curr_price
        )

        # 6. Composite Score
        score, bias, reasons = compute_composite_score(
            ema=ema_result,
            macd=macd_result,
            vwap=vwap_result,
            pivots=pivots_result,
            sr=sr_result,
            current_price=curr_price,
        )

        return IndicatorSnapshot(
            symbol=symbol,
            timeframe=timeframe,
            timestamp=last_time,
            current_price=curr_price,
            candle_count=n,
            ema=ema_result,
            macd=macd_result,
            vwap=vwap_result,
            pivots=pivots_result,
            support_resistance=sr_result,
            technical_score=score,
            technical_bias=bias,
            summary_reasons=reasons,
        )

    def get_snapshot(
        self,
        symbol: str,
        timeframe: str = "5m",
    ) -> Optional[IndicatorSnapshot]:
        """Calculates snapshot directly from MarketDataEngine ring buffer."""
        buf = market_engine.get_buffer(symbol, timeframe)
        if not buf or len(buf) < 2:
            return None

        closed_candles = [c for c in buf.get_all() if c.get("is_closed", True)]
        if len(closed_candles) < 2:
            return None
        opens = [c["open"] for c in closed_candles]
        highs = [c["high"] for c in closed_candles]
        lows = [c["low"] for c in closed_candles]
        closes = [c["close"] for c in closed_candles]
        volumes = [c["volume"] for c in closed_candles]
        times = [c["open_time"] for c in closed_candles]

        return self.calculate_from_series(
            symbol=symbol,
            timeframe=timeframe,
            opens=opens,
            highs=highs,
            lows=lows,
            closes=closes,
            volumes=volumes,
            timestamps=times,
        )

    def get_multi_timeframe_snapshot(
        self,
        symbol: str,
        timeframes: Optional[List[str]] = None,
    ) -> MultiTimeframeSnapshot:
        """Computes indicator snapshots across all specified timeframes (default: 5m, 15m, 1h)."""
        target_tfs = timeframes or ["5m", "15m", "1h"]
        snapshots: Dict[str, IndicatorSnapshot] = {}
        total_score = 0
        valid_count = 0
        latest_price = 0.0

        for tf in target_tfs:
            snap = self.get_snapshot(symbol, tf)
            if snap:
                snapshots[tf] = snap
                total_score += snap.technical_score
                valid_count += 1
                latest_price = snap.current_price

        # If live price exists in market cache, prefer it
        live_price = market_engine.get_live_price(symbol)
        if live_price:
            latest_price = live_price.get("mark_price") or live_price.get("spot_price")

        agg_score = int(round(total_score / valid_count)) if valid_count > 0 else 0
        if agg_score >= 45:
            agg_bias = "STRONG_BULLISH"
        elif agg_score >= 15:
            agg_bias = "BULLISH"
        elif agg_score <= -45:
            agg_bias = "STRONG_BEARISH"
        elif agg_score <= -15:
            agg_bias = "BEARISH"
        else:
            agg_bias = "NEUTRAL"

        from datetime import datetime, timezone

        return MultiTimeframeSnapshot(
            symbol=symbol,
            current_price=latest_price,
            updated_at=datetime.now(timezone.utc).isoformat(),
            timeframes=snapshots,
            aggregate_bias=agg_bias,
            aggregate_score=agg_score,
        )


# Global singleton
indicator_engine = IndicatorEngine()
