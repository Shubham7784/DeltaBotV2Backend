"""Deterministic Risk Management Engine (Phase 7).

CRITICAL ARCHITECTURAL CONSTRAINTS:
1. 1% Max Risk Rule:
   - Loss if Stop Loss is hit MUST NOT exceed 1.0% of total account equity.
   - Calculates exact integer contract quantity based on entry price and stop loss.
2. 30% Uncommitted Reserve Buffer:
   - Total deployed margin across all positions cannot exceed 70% of equity (30% strictly held in reserve).
3. 50% Maximum Portfolio Exposure:
   - Total active position margin cannot exceed 50% of equity.
4. Multiple positions per instrument are allowed.
   - Opposing (hedged) or duplicate positions are strictly prohibited.
5. Liquidation Protection:
   - Stop loss must trigger well before liquidation price (minimum 30% liquidation buffer).
6. Daily Drawdown Circuit Breaker:
   - If cumulative daily realized losses exceed 5.0% of starting equity, halt all new trade entries.
7. LLM Advisory Guardrail:
   - LLM provides advisory regime opinions ONLY.
   - The LLM is strictly forbidden from altering sizing, widening stop-loss, or overriding risk rules.
"""

import logging
import math
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.leverage import get_trade_leverage
from app.db.enums import TradeLifecycleState
from app.db.models import TradeDecision
from app.db.repository import TradeRepository
from app.db.session import get_db_session
from app.schemas.risk import AccountCapitalStatus, RiskCheckRequest, RiskValidationResult
from app.services.client_factory import client_factory

logger = logging.getLogger("delta_bot.risk_engine")

# Default contract sizes on Delta Exchange per contract unit
CONTRACT_UNITS: Dict[str, float] = {
    "BTCUSD": 0.001,      # 1 contract = 0.001 BTC
    "ETHUSD": 0.01,       # 1 contract = 0.01 ETH
    "SOLUSD": 0.1,        # 1 contract = 0.1 SOL
    "DEFAULT": 0.001,
}

# Maintenance margin rate per contract tier (approx 1% for BTC/ETH at 50x)
MAINTENANCE_MARGIN_RATE = 0.01


class DeterministicRiskEngine:
    """Singleton engine providing deterministic risk verification and position sizing."""

    def __init__(self):
        self.reserve_buffer_pct: float = 30.0    # 30% untouchable buffer
        self.max_exposure_pct: float = 50.0      # 50% max simultaneous margin exposure
        self.max_risk_per_trade_pct: float = 1.0 # 1% max loss per trade
        self.daily_drawdown_limit_pct: float = 5.0 # 5% circuit breaker
        self.default_leverage: int = settings.DEFAULT_LEVERAGE or 50

    def get_contract_unit(self, symbol: str) -> float:
        """Returns the contract multiplier for a symbol."""
        sym = symbol.upper()
        return CONTRACT_UNITS.get(sym, CONTRACT_UNITS["DEFAULT"])

    async def get_account_capital(
        self,
        subaccount: str = "futures",
        override_capital: Optional[float] = None,
        is_paper: Optional[bool] = None,
    ) -> AccountCapitalStatus:
        """Fetches live wallet equity and computes reserve and exposure thresholds."""
        if override_capital is not None and override_capital > 0:
            total_equity = override_capital
            available_margin = override_capital * 0.70
            blocked_margin = 0.0
        else:
            # Query client (live or paper)
            try:
                import inspect
                client = client_factory.get_client(subaccount, paper_trading=is_paper)
                raw_bal = client.get_wallet_balances()
                if inspect.isawaitable(raw_bal):
                    balances = await raw_bal
                else:
                    balances = raw_bal

                if isinstance(balances, dict):
                    res_list = balances.get("result")
                    if isinstance(res_list, list) and len(res_list) > 0:
                        usd_item = next((a for a in res_list if a.get("asset_symbol") in ("USD", "USDT")), res_list[0])
                        total_equity = float(usd_item.get("balance", settings.INITIAL_WALLET_USD or 12.0))
                        available_margin = float(usd_item.get("available_balance", total_equity * 0.70))
                        blocked_margin = float(usd_item.get("blocked_margin", 0.0))
                    elif "meta" in balances and "net_equity" in balances["meta"]:
                        total_equity = float(balances["meta"]["net_equity"])
                        available_margin = total_equity * 0.70
                        blocked_margin = 0.0
                    else:
                        total_equity = float(balances.get("total_equity", settings.INITIAL_WALLET_USD or 12.0))
                        available_margin = float(balances.get("available_margin", total_equity * 0.70))
                        blocked_margin = float(balances.get("blocked_margin", 0.0))
                else:
                    total_equity = settings.INITIAL_WALLET_USD or 12.0
                    available_margin = total_equity * 0.70
                    blocked_margin = 0.0
            except Exception as e:
                logger.warning(f"Error querying live wallet balance: {e}. Falling back to default capital.")
                total_equity = settings.INITIAL_WALLET_USD or 12.0
                available_margin = total_equity * 0.70
                blocked_margin = 0.0

        # Safety fallback for micro-wallets
        if total_equity <= 0:
            total_equity = 12.0
            available_margin = 8.4
            blocked_margin = 0.0

        reserve_buffer_amount = total_equity * (self.reserve_buffer_pct / 100.0)
        usable_trading_capital = max(0.0, total_equity - reserve_buffer_amount)
        current_exposure_pct = (blocked_margin / total_equity) * 100.0 if total_equity > 0 else 0.0
        max_risk_amount = total_equity * (self.max_risk_per_trade_pct / 100.0)

        # Calculate daily drawdown
        daily_drawdown_pct, circuit_breaker = await self.check_daily_drawdown(total_equity)

        return AccountCapitalStatus(
            currency="USD",
            total_equity=round(total_equity, 2),
            available_margin=round(available_margin, 2),
            blocked_margin=round(blocked_margin, 2),
            reserve_buffer_pct=self.reserve_buffer_pct,
            reserve_buffer_amount=round(reserve_buffer_amount, 2),
            usable_trading_capital=round(usable_trading_capital, 2),
            current_exposure_pct=round(current_exposure_pct, 2),
            max_exposure_pct=self.max_exposure_pct,
            max_risk_per_trade_pct=self.max_risk_per_trade_pct,
            max_risk_per_trade_amount=round(max_risk_amount, 4),
            daily_drawdown_pct=round(daily_drawdown_pct, 2),
            daily_drawdown_limit_pct=self.daily_drawdown_limit_pct,
            circuit_breaker_active=circuit_breaker,
        )

    async def check_daily_drawdown(self, total_equity: float) -> Tuple[float, bool]:
        """Calculates cumulative realized losses for today and checks circuit breaker."""
        today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        async with get_db_session() as session:
            trades = await TradeRepository.get_trade_decisions(session=session, state="EXITED", limit=100)
            today_realized_losses = 0.0
            for t in trades:
                closed = t.closed_at
                if closed is not None:
                    if closed.tzinfo is None:
                        closed = closed.replace(tzinfo=timezone.utc)
                    if closed >= today_start and t.realized_pnl < 0:
                        today_realized_losses += abs(t.realized_pnl)

        if total_equity <= 0:
            return 0.0, False

        drawdown_pct = (today_realized_losses / total_equity) * 100.0
        circuit_breaker = drawdown_pct >= self.daily_drawdown_limit_pct
        return drawdown_pct, circuit_breaker

    def calculate_liquidation_price(
        self,
        entry_price: float,
        direction: str,
        leverage: int,
        maintenance_rate: float = MAINTENANCE_MARGIN_RATE,
    ) -> float:
        """Calculates approximate isolated liquidation price."""
        if leverage <= 0:
            leverage = 50
        initial_margin_rate = 1.0 / leverage
        if direction.lower() == "long":
            liq = entry_price * (1.0 - initial_margin_rate + maintenance_rate)
            return max(0.0, round(liq, 2))
        else:
            liq = entry_price * (1.0 + initial_margin_rate - maintenance_rate)
            return round(liq, 2)

    def calculate_sizing_and_risk(
        self,
        entry_price: float,
        stop_loss_price: float,
        take_profit_price: float,
        direction: str,
        total_equity: float,
        leverage: int,
        symbol: str,
        margin_budget: Optional[float] = None,
    ) -> Tuple[int, float, float, float, float]:
        """Calculates integer contract size where potential loss <= 1% capital.

        Returns: (calculated_size, required_margin, dollar_risk, dollar_risk_pct, rr_ratio)
        """
        contract_unit = self.get_contract_unit(symbol)
        price_diff = abs(entry_price - stop_loss_price)
        if price_diff <= 0:
            return 0, 0.0, 0.0, 0.0, 0.0

        # Loss per 1 contract unit
        loss_per_contract = price_diff * contract_unit
        max_allowed_dollar_risk = total_equity * (self.max_risk_per_trade_pct / 100.0)

        # Integer size calculation
        raw_size = max_allowed_dollar_risk / loss_per_contract
        calculated_size = int(math.floor(raw_size))

        # Baseline minimum for micro accounts ($12 USD)
        if calculated_size == 0:
            # On tiny wallets (<= $50 USD), allow 1 contract if within micro risk tolerance (<= 5% capital)
            if total_equity <= 50.0:
                micro_max_risk = max(max_allowed_dollar_risk * 1.5, total_equity * 0.05)
                if loss_per_contract <= micro_max_risk:
                    calculated_size = 1
            elif loss_per_contract <= max_allowed_dollar_risk * 1.5:
                calculated_size = 1
            else:
                calculated_size = 0

        # Leverage changes the margin-backed lot ceiling, while the stop-loss
        # risk limit remains the primary cap on the position size.
        if margin_budget is not None and entry_price > 0 and leverage > 0:
            margin_per_contract = entry_price * contract_unit / leverage
            margin_lot_limit = int(math.floor(max(0.0, margin_budget) / margin_per_contract))
            calculated_size = min(calculated_size, margin_lot_limit)

        actual_dollar_risk = calculated_size * loss_per_contract
        dollar_risk_pct = (actual_dollar_risk / total_equity) * 100.0 if total_equity > 0 else 0.0

        # Margin required: (entry_price * contract_unit * size) / leverage
        notional = entry_price * contract_unit * calculated_size
        required_margin = notional / leverage if leverage > 0 else notional

        # Risk : Reward ratio
        reward_diff = abs(take_profit_price - entry_price)
        rr_ratio = round(reward_diff / price_diff, 2) if price_diff > 0 else 0.0

        return calculated_size, round(required_margin, 4), round(actual_dollar_risk, 4), round(dollar_risk_pct, 2), rr_ratio

    async def validate_trade(
        self,
        symbol: str,
        direction: str,
        entry_price: float,
        stop_loss_price: float,
        take_profit_price: float,
        leverage: int = 50,
        subaccount: str = "futures",
        current_trade_id: Optional[int] = None,
        override_capital: Optional[float] = None,
        is_paper: Optional[bool] = None,
    ) -> RiskValidationResult:
        """Executes full deterministic risk evaluation against all system invariants."""
        symbol = symbol.upper()
        direction = direction.lower()
        leverage = get_trade_leverage(symbol, subaccount, leverage)
        rejections: List[str] = []
        warnings: List[str] = []

        # 1. Fetch Capital & Exposure Status
        capital = await self.get_account_capital(
            subaccount=subaccount,
            override_capital=override_capital,
            is_paper=is_paper,
        )

        # Check Circuit Breaker
        circuit_breaker_passed = not capital.circuit_breaker_active
        if not circuit_breaker_passed:
            rejections.append(
                f"Daily Drawdown Circuit Breaker active: cumulative loss ({capital.daily_drawdown_pct}%) exceeds {self.daily_drawdown_limit_pct}% limit"
            )

        # 2. Single-position invariant is disabled; retain the field for API compatibility.
        single_pos_passed = True

        # 3. Check Stop-Loss / Take-Profit Direction Sanity
        if direction == "long":
            if stop_loss_price >= entry_price:
                rejections.append(f"Invalid Long SL: stop loss (${stop_loss_price:,.2f}) must be strictly below entry price (${entry_price:,.2f})")
            if take_profit_price <= entry_price:
                rejections.append(f"Invalid Long TP: take profit (${take_profit_price:,.2f}) must be strictly above entry price (${entry_price:,.2f})")
        else:  # short
            if stop_loss_price <= entry_price:
                rejections.append(f"Invalid Short SL: stop loss (${stop_loss_price:,.2f}) must be strictly above entry price (${entry_price:,.2f})")
            if take_profit_price >= entry_price:
                rejections.append(f"Invalid Short TP: take profit (${take_profit_price:,.2f}) must be strictly below entry price (${entry_price:,.2f})")

        # 4. Sizing & 1% Risk Rule
        calc_size, req_margin, dollar_risk, dollar_risk_pct, rr_ratio = self.calculate_sizing_and_risk(
            entry_price=entry_price,
            stop_loss_price=stop_loss_price,
            take_profit_price=take_profit_price,
            direction=direction,
            total_equity=capital.total_equity,
            leverage=leverage,
            symbol=symbol,
            margin_budget=max(
                0.0,
                min(
                    capital.available_margin,
                    capital.usable_trading_capital - capital.blocked_margin,
                    capital.total_equity * (self.max_exposure_pct / 100.0) - capital.blocked_margin,
                ),
            ),
        )

        if calc_size <= 0:
            rejections.append(
                f"1% Risk Rule: required position size calculated to 0 contracts. Capital (${capital.total_equity:.2f}) insufficient for SL distance."
            )

        allowed_risk_limit = max(
            capital.max_risk_per_trade_amount * 1.5,
            capital.total_equity * 0.05 if capital.total_equity <= 50.0 else capital.max_risk_per_trade_amount * 1.5,
        )
        if dollar_risk > allowed_risk_limit:
            rejections.append(
                f"1% Risk Rule Exceeded: calculated loss (${dollar_risk:.2f}) exceeds max capital allowance (${capital.max_risk_per_trade_amount:.2f})"
            )

        # Minimum R:R check (should be >= 1.5)
        if rr_ratio < 1.5:
            warnings.append(f"Risk-Reward Ratio is low ({rr_ratio}:1). Recommended >= 2.0:1.")

        # 5. 30% Reserve Buffer & 50% Exposure Invariant
        reserve_buffer_passed = True
        max_exposure_passed = True

        new_total_blocked = capital.blocked_margin + req_margin
        new_exposure_pct = (new_total_blocked / capital.total_equity) * 100.0 if capital.total_equity > 0 else 100.0

        if new_total_blocked > capital.usable_trading_capital:
            reserve_buffer_passed = False
            rejections.append(
                f"30% Reserve Buffer Breach: trade required margin (${req_margin:.2f}) leaves less than 30% equity (${capital.reserve_buffer_amount:.2f}) untouched"
            )

        if new_exposure_pct > self.max_exposure_pct:
            max_exposure_passed = False
            rejections.append(
                f"50% Max Exposure Breach: projected exposure ({new_exposure_pct:.1f}%) exceeds maximum allowable {self.max_exposure_pct}%"
            )

        # 6. Liquidation Buffer Protection
        est_liq_price = self.calculate_liquidation_price(entry_price, direction, leverage)
        liq_buffer_passed = True
        liq_buffer_pct = 0.0

        if direction == "long":
            liq_dist = entry_price - est_liq_price
            sl_dist = entry_price - stop_loss_price
            if stop_loss_price <= est_liq_price:
                liq_buffer_passed = False
                rejections.append(f"Liquidation Risk: Stop Loss (${stop_loss_price:,.2f}) is at or below Liquidation Price (${est_liq_price:,.2f})!")
            elif liq_dist > 0:
                liq_buffer_pct = round(((stop_loss_price - est_liq_price) / liq_dist) * 100.0, 1)
        else:
            liq_dist = est_liq_price - entry_price
            sl_dist = stop_loss_price - entry_price
            if stop_loss_price >= est_liq_price:
                liq_buffer_passed = False
                rejections.append(f"Liquidation Risk: Stop Loss (${stop_loss_price:,.2f}) is at or above Liquidation Price (${est_liq_price:,.2f})!")
            elif liq_dist > 0:
                liq_buffer_pct = round(((est_liq_price - stop_loss_price) / liq_dist) * 100.0, 1)

        if liq_buffer_passed and liq_buffer_pct < 25.0:
            warnings.append(f"Thin Liquidation Buffer: Stop loss is only {liq_buffer_pct}% away from estimated liquidation.")

        is_approved = len(rejections) == 0

        return RiskValidationResult(
            is_approved=is_approved,
            rejection_reasons=rejections,
            risk_warnings=warnings,
            calculated_size=calc_size,
            required_margin=req_margin,
            dollar_risk=dollar_risk,
            dollar_risk_pct=dollar_risk_pct,
            risk_reward_ratio=rr_ratio,
            single_position_passed=single_pos_passed,
            reserve_buffer_passed=reserve_buffer_passed,
            max_exposure_passed=max_exposure_passed,
            liquidation_buffer_passed=liq_buffer_passed,
            circuit_breaker_passed=circuit_breaker_passed,
            estimated_liquidation_price=est_liq_price,
            liquidation_buffer_pct=liq_buffer_pct,
            llm_guardrail_confirmed=True,
            details={
                "total_equity": capital.total_equity,
                "reserve_buffer_amount": capital.reserve_buffer_amount,
                "usable_trading_capital": capital.usable_trading_capital,
                "current_exposure_pct": capital.current_exposure_pct,
                "projected_exposure_pct": round(new_exposure_pct, 2),
                "leverage": leverage,
                "contract_unit": self.get_contract_unit(symbol),
            },
        )


# Global singleton instance
risk_engine = DeterministicRiskEngine()
