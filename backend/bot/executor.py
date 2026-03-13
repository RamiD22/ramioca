"""Order execution engine with safety checks — v2.

Improvements:
  - Records outcomes for streak tracking
  - Better price handling (use bid/ask, not mid)
  - Enhanced logging for dashboard activity feed
  - Per-agent strategy state support for dual-agent competition
  - Per-market cooldown to prevent duplicate order spam
  - Falls back to market order on "crosses the book" errors
"""

from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Callable

from backend.bot.client import polymarket
from backend.bot.risk import calculate_position_size, should_stop_loss, should_take_profit
from backend.bot.strategy import record_outcome
from backend.config import settings
from backend.models import (
    DashboardState,
    Position,
    Side,
    StrategyOutput,
    TradeRecord,
)

if TYPE_CHECKING:
    from backend.bot.agent import StrategyState

logger = logging.getLogger(__name__)

# Cooldown between orders on the SAME token.
# 2s cooldown — trade every other cycle on 5-min windows
_ORDER_COOLDOWN_5M = 2
_ORDER_COOLDOWN_DEFAULT = 180  # 3 minutes for standard markets


class Executor:
    def __init__(
        self,
        state: DashboardState,
        strategy_state: "StrategyState | None" = None,
        max_exposure: float | None = None,
    ) -> None:
        self.state = state
        self.strategy_state = strategy_state
        self.max_exposure = max_exposure  # Per-agent budget cap
        self._pending_trades: dict[str, TradeRecord] = {}  # token_id → trade
        self._order_timestamps: dict[str, float] = {}  # token_id → last order time

    def execute_signal(self, signal: StrategyOutput) -> TradeRecord | None:
        """Execute a trade based on strategy signal, with risk checks."""
        if signal.recommended_side is None:
            return None

        # ── Dedup: skip if we already ordered this market recently ──
        # 5-min markets get shorter cooldown to allow scaling in
        is_5m = "up or down" in signal.market.lower()
        cooldown = _ORDER_COOLDOWN_5M if is_5m else _ORDER_COOLDOWN_DEFAULT

        now = time.time()
        last_order = self._order_timestamps.get(signal.token_id, 0)
        if now - last_order < cooldown:
            logger.debug(f"Skipping {signal.market[:40]} — ordered {now - last_order:.0f}s ago (cooldown={cooldown}s)")
            return None

        balance = self.state.metrics.balance
        exposure = self.state.metrics.total_exposure
        active = self.state.metrics.active_positions

        size = calculate_position_size(
            signal, balance, exposure, active,
            max_exposure=self.max_exposure,
        )
        if size <= 0:
            return None

        side = signal.recommended_side.value

        # For BUY orders, we want to buy at or below market price
        # For SELL (buying the NO token), use the complementary price
        if side == "BUY":
            price = signal.market_price
        else:
            price = 1.0 - signal.market_price

        # Round size to whole dollar for market orders (avoids decimal precision errors)
        size = round(size)

        logger.info(
            f"EXECUTING: {side} ${size} @ {price:.4f} | "
            f"edge={signal.edge:.3f} [{signal.composite_signal.value}] | "
            f"{signal.market[:55]}"
        )

        # ── Pre-flight: use fresh order book price for execution ──
        # The signal price may be stale (Opus takes 5-18s to respond).
        # Always use the live CLOB price for execution decisions.
        try:
            book_price = polymarket.get_price(signal.token_id, "BUY")
            if book_price and book_price > 0:
                # Hard-reject at true extremes only (>0.92 or <0.08).
                # 15m/daily markets legitimately trade above 0.85 when
                # the outcome becomes clearer — don't block those.
                if book_price > 0.92 or book_price < 0.08:
                    logger.warning(
                        f"Order book price {book_price:.4f} at extreme — no edge, skipping"
                    )
                    return None
                # Use the fresh book price for execution
                if abs(book_price - price) / max(price, 0.01) > 0.05:
                    logger.info(
                        f"Updating execution price: {price:.4f} → {book_price:.4f} (fresh CLOB)"
                    )
                    price = book_price
        except Exception as e:
            logger.warning(f"Pre-flight price check failed: {e}")
            # Continue with signal price — the client.py price guard will catch it

        result = None
        try:
            # ── Market order first (instant fill) ──
            result = polymarket.place_market_order(
                token_id=signal.token_id,
                amount=size,
                side="BUY",  # always BUY the selected token (YES or NO)
            )
        except Exception as e:
            err_msg = str(e).lower()
            if "duplicated" in err_msg:
                logger.warning("Order duplicated — skipping")
                result = None
            elif "crosses the book" in err_msg:
                # Our aggressive price went too far — already handled by CLOB
                logger.warning(f"Order crosses book — adjusting: {e}")
                result = None
            else:
                logger.error(f"Order placement error: {e}")
                result = None

        if result is None:
            logger.error("Order placement failed — no response")
            # Don't record as loss — API failure (e.g., geoblock) is not a trade loss
            return None

        # Record cooldown timestamp AFTER successful placement
        self._order_timestamps[signal.token_id] = now

        order_id = result.get("orderID", result.get("id", str(uuid.uuid4())))

        trade = TradeRecord(
            id=order_id,
            timestamp=datetime.now(timezone.utc),
            market=signal.market,
            side=Side(side),
            price=price,
            size=size,
            token_id=signal.token_id,
            status="dry_run" if settings.DRY_RUN else "placed",
        )

        self.state.recent_trades.insert(0, trade)
        self.state.recent_trades = self.state.recent_trades[:100]
        self.state.metrics.total_trades += 1

        # Track for outcome resolution
        self._pending_trades[signal.token_id] = trade

        return trade

    def check_stop_losses(self) -> list[str]:
        """Check all active positions for stop-loss and take-profit triggers.

        For 5-min binary markets (entry ~0.50), positions resolve to $1 or $0.
        Stop losses only help if:
        - We're early enough in the window (price still has room to move)
        - The recovery value is meaningful (price > 0.15)
        If price is already < 0.15, holding is better (20% chance of $1 > selling for 15¢).
        """
        stopped = []
        for pos in self.state.positions:
            if pos.current_price <= 0:
                continue  # Skip resolved positions

            # ── Skip already-resolved positions (at $0 or $1) ──
            # These tokens have settled — the orderbook no longer exists
            if pos.current_price >= 0.95 or pos.current_price <= 0.05:
                continue

            # ── Skip if recovery value is too low ──
            # For binary markets at < $0.15, holding gives better EV than selling
            if pos.current_price < 0.15:
                logger.debug(
                    f"Skip stop loss on {pos.market[:40]} — "
                    f"price={pos.current_price:.3f} too low to recover (letting it ride)"
                )
                continue

            # Stop loss
            if should_stop_loss(pos.avg_price, pos.current_price, pos.side):
                logger.warning(
                    f"STOP LOSS on {pos.market[:40]} | "
                    f"entry={pos.avg_price:.3f} current={pos.current_price:.3f}"
                )
                try:
                    result = polymarket.place_market_order(
                        token_id=pos.token_id,
                        amount=pos.size,
                        side="SELL",
                    )
                    if result:
                        stopped.append(pos.condition_id)
                        record_outcome(False, self.strategy_state)
                except Exception as e:
                    logger.warning(f"Stop loss sell failed for {pos.market[:40]}: {e}")
                    # Don't crash — position will settle naturally

            # Take profit
            elif should_take_profit(pos.avg_price, pos.current_price, pos.side):
                logger.info(
                    f"TAKE PROFIT on {pos.market[:40]} | "
                    f"entry={pos.avg_price:.3f} current={pos.current_price:.3f}"
                )
                try:
                    result = polymarket.place_market_order(
                        token_id=pos.token_id,
                        amount=pos.size,
                        side="SELL",
                    )
                    if result:
                        stopped.append(pos.condition_id)
                        record_outcome(True, self.strategy_state)
                except Exception as e:
                    logger.warning(f"Take profit sell failed for {pos.market[:40]}: {e}")

        return stopped
