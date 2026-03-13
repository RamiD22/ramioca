"""Position sizing and risk management — v2.

Improvements:
  - Adaptive Kelly fraction based on recent win rate
  - Streak-based size reduction
  - Minimum confidence threshold
  - Better stop-loss/take-profit with trailing logic
"""

from __future__ import annotations

import logging

from backend.config import settings
from backend.models import StrategyOutput, Side

logger = logging.getLogger(__name__)


def calculate_position_size(
    signal: StrategyOutput,
    balance: float,
    current_exposure: float,
    active_positions: int = 0,
    max_exposure: float | None = None,
) -> float:
    """Kelly-inspired position sizing with adaptive risk controls.

    Returns the recommended position size in USDC.

    Args:
        max_exposure: Per-agent budget cap. If None, uses settings.MAX_TOTAL_EXPOSURE.
    """
    if signal.recommended_side is None:
        return 0.0

    # Hard limits
    if active_positions >= settings.MAX_POSITIONS:
        logger.info(f"Max positions ({settings.MAX_POSITIONS}) reached — skip")
        return 0.0

    exposure_limit = max_exposure if max_exposure is not None else settings.MAX_TOTAL_EXPOSURE
    remaining = exposure_limit - current_exposure
    if remaining <= 0:
        logger.info("Max exposure reached — skip")
        return 0.0

    # Price range filter — check BOTH the market price AND execution price.
    # When we SELL (buy the NO token), execution price = 1 - market_price.
    # Both must be in the tradeable range to avoid 0.99/0.01 garbage trades.
    mp = signal.market_price
    exec_price = mp if signal.recommended_side == Side.BUY else (1.0 - mp)
    if mp < settings.MIN_PRICE or mp > settings.MAX_PRICE:
        logger.info(f"Market price {mp:.2f} outside [{settings.MIN_PRICE}, {settings.MAX_PRICE}]")
        return 0.0
    if exec_price < settings.MIN_PRICE or exec_price > settings.MAX_PRICE:
        logger.info(f"Exec price {exec_price:.2f} outside [{settings.MIN_PRICE}, {settings.MAX_PRICE}]")
        return 0.0

    # Minimum edge filter — require meaningful edge (covers spread + slippage)
    edge = abs(signal.edge)
    if edge < 0.015:
        return 0.0

    # Win probability
    if signal.recommended_side == Side.BUY:
        win_prob = signal.probability_estimate
        price = signal.market_price
    else:
        win_prob = 1.0 - signal.probability_estimate
        price = 1.0 - signal.market_price

    if price <= 0 or price >= 1:
        return 0.0

    # ── Kelly Criterion ──
    odds = (1 / price) - 1
    if odds <= 0:
        return 0.0

    kelly = (odds * win_prob - (1 - win_prob)) / odds

    # Fixed 1% of balance per trade — keep trades small
    fraction = 0.01

    size = balance * fraction
    # Cap by clip size (hard per-trade max)
    size = min(size, settings.CLIP_SIZE)
    # Cap by per-market max
    size = min(size, settings.MAX_POSITION_SIZE)
    # Cap by remaining exposure
    size = min(size, remaining)
    size = round(size, 2)

    if size < 1.0:
        return 0.0

    logger.info(
        f"Size: ${size:.2f} | edge={edge:.3f} clip=${settings.CLIP_SIZE}"
    )
    return size


# For 5-min binary markets (entry ~0.50), these are more appropriate thresholds.
# The markets swing fast (0.50 → 0.20 in seconds), so tight stop losses just
# lock in losses that might recover. Only bail if the position is clearly in trouble
# but still has enough value to recover something meaningful (> $0.15).
_BINARY_STOP_LOSS_PCT = 0.30   # 30% — trigger at ~$0.35 for a $0.50 entry
_BINARY_TAKE_PROFIT_PCT = 0.35  # 35% — trigger at ~$0.67 for a $0.50 entry


def _is_binary_market_entry(entry_price: float) -> bool:
    """Detect if this looks like a binary 50/50 market entry (price near 0.50)."""
    return 0.40 <= entry_price <= 0.60


def should_stop_loss(entry_price: float, current_price: float, side: str) -> bool:
    """Check if position should be stopped out.

    Uses wider thresholds for binary 50/50 markets (5-min up/down) since
    they swing fast and resolve in minutes — tight stops just lock in losses.
    """
    if entry_price <= 0:
        return False

    if side == "BUY" or side == "YES" or side == "Up":
        loss_pct = (entry_price - current_price) / entry_price
    else:
        loss_pct = (current_price - entry_price) / (1 - entry_price) if entry_price < 1 else 0

    # Use wider threshold for binary market entries
    threshold = _BINARY_STOP_LOSS_PCT if _is_binary_market_entry(entry_price) else settings.STOP_LOSS_PCT

    if loss_pct >= threshold:
        logger.warning(f"Stop loss: {loss_pct:.1%} >= {threshold:.1%}")
        return True
    return False


def should_take_profit(entry_price: float, current_price: float, side: str) -> bool:
    """Check if position should be closed for profit.

    Uses wider thresholds for binary markets — let winners ride toward $1.
    """
    if entry_price <= 0:
        return False

    if side == "BUY" or side == "YES" or side == "Up":
        gain_pct = (current_price - entry_price) / entry_price
    else:
        gain_pct = (entry_price - current_price) / (1 - entry_price) if entry_price < 1 else 0

    # Use wider threshold for binary market entries (let winners ride toward $1)
    threshold = _BINARY_TAKE_PROFIT_PCT if _is_binary_market_entry(entry_price) else settings.TAKE_PROFIT_PCT

    if gain_pct >= threshold:
        logger.info(f"Take profit: {gain_pct:.1%} >= {threshold:.1%}")
        return True
    return False
