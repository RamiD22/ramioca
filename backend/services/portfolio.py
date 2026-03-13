"""Portfolio tracking, PnL calculation, and performance metrics."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import math

import numpy as np

from backend.bot.client import polymarket
from backend.models import DashboardState, PortfolioMetrics, Position

logger = logging.getLogger(__name__)


def _safe(v: float) -> float:
    """Convert NaN/Inf to 0.0 for JSON-safe serialization."""
    if math.isnan(v) or math.isinf(v):
        return 0.0
    return v


def compute_metrics_from_polymarket(
    raw_positions: list[dict],
    raw_trades: list[dict],
    balance: float = 0.0,
) -> PortfolioMetrics:
    """Compute metrics from REAL Polymarket API data only.

    raw_positions: from Data API /positions (contains cashPnl, size, curPrice)
    raw_trades: from Data API /trades (contains actual executed trades)
    """
    # PnL: sum of cashPnl from all real positions
    total_pnl = 0.0
    total_exposure = 0.0
    active_positions = 0
    winning_positions = 0
    losing_positions = 0

    for p in raw_positions:
        size = float(p.get("size", 0))
        if size < 0.01:
            continue

        cur_price = float(p.get("curPrice", 0))
        cash_pnl = float(p.get("cashPnl", p.get("cashPnL", 0)))

        total_pnl += cash_pnl

        if cur_price > 0:
            total_exposure += size * cur_price
            active_positions += 1

        if cash_pnl > 0:
            winning_positions += 1
        elif cash_pnl < 0:
            losing_positions += 1

    resolved = winning_positions + losing_positions
    win_rate = winning_positions / resolved if resolved > 0 else 0.0

    total_trades = len(raw_trades)

    return PortfolioMetrics(
        balance=_safe(balance),
        total_pnl=_safe(round(total_pnl, 2)),
        total_pnl_pct=_safe(round((total_pnl / balance) * 100, 2)) if balance > 0 else 0.0,
        win_rate=_safe(round(win_rate, 4)),
        total_trades=total_trades,
        winning_trades=winning_positions,
        losing_trades=losing_positions,
        active_positions=active_positions,
        total_exposure=_safe(round(total_exposure, 2)),
    )


def fetch_raw_trades() -> list[dict]:
    """Fetch real trades from Polymarket Data API."""
    try:
        return polymarket.get_trades()
    except Exception as e:
        logger.error(f"Failed to fetch trades: {e}")
        return []


def fetch_raw_positions() -> list[dict]:
    """Fetch all positions from Polymarket Data API (once per cycle)."""
    try:
        return polymarket.get_positions()
    except Exception as e:
        logger.error(f"Failed to fetch positions: {e}")
        return []


def sync_positions_for_agent(
    state: DashboardState,
    owned_token_ids: set[str],
    raw_positions: list[dict],
    owned_sizes: dict[str, float] | None = None,
) -> None:
    """Sync positions for a specific agent, filtered by ownership.

    Only includes positions whose token_id (asset) is in owned_token_ids.
    If owned_token_ids is empty, no positions are assigned (agent hasn't traded yet).
    When owned_sizes is provided, proportions the wallet position by the agent's invested $.
    """
    try:
        positions: list[Position] = []

        for p in raw_positions:
            wallet_size = float(p.get("size", 0))
            if wallet_size < 0.01:
                continue

            cur_price = float(p.get("curPrice", 0))

            token_id = p.get("asset", "")

            # Only include positions owned by this agent
            if token_id not in owned_token_ids:
                continue

            # Proportion the position by what this agent invested
            # (prevents double-counting when both agents trade the same token)
            if owned_sizes and token_id in owned_sizes:
                wallet_value = float(p.get("initialValue", 0))
                if wallet_value > 0:
                    agent_ratio = min(owned_sizes[token_id] / wallet_value, 1.0)
                else:
                    agent_ratio = 1.0
            else:
                agent_ratio = 1.0

            size = wallet_size * agent_ratio
            if size < 0.01:
                continue

            cash_pnl = float(p.get("cashPnl", p.get("cashPnL", 0))) * agent_ratio
            pct_pnl = float(p.get("percentPnl", p.get("percentPnL", 0)))

            pos = Position(
                market=p.get("title", "Unknown"),
                condition_id=p.get("conditionId", ""),
                token_id=token_id,
                side=p.get("outcome", "YES"),
                size=round(size, 4),
                avg_price=float(p.get("avgPrice", 0)),
                current_price=cur_price,
                pnl=round(cash_pnl, 4),
                pnl_pct=round(pct_pnl, 2),
            )
            positions.append(pos)

        state.positions = positions
        active = sum(1 for p in positions if p.current_price > 0)
        logger.info(f"Synced {len(positions)} positions for agent (active: {active})")

    except Exception as e:
        logger.error(f"Failed to sync positions: {e}")


def cleanup_settled_positions(
    raw_positions: list[dict],
    owned_token_ids: set[str],
) -> set[str]:
    """Detect settled/redeemed positions and return token_ids to remove.

    A position is settled when:
    - Its current price is <= 0.01 or >= 0.99 (market resolved to 0 or 1)
    - Its wallet size has dropped to near-zero (redeemed)
    - It no longer appears in the API at all
    """
    to_remove: set[str] = set()

    # Build lookup of current API positions
    api_tokens: dict[str, dict] = {}
    for p in raw_positions:
        token_id = p.get("asset", "")
        if token_id:
            api_tokens[token_id] = p

    for token_id in owned_token_ids:
        pos_data = api_tokens.get(token_id)

        if pos_data is None:
            # Token no longer in API — already redeemed/settled
            to_remove.add(token_id)
            continue

        cur_price = float(pos_data.get("curPrice", 0))
        wallet_size = float(pos_data.get("size", 0))

        # Settled: price at extreme indicates market resolved
        if cur_price <= 0.01 or cur_price >= 0.99:
            to_remove.add(token_id)
            continue

        # Wallet size near zero — position redeemed
        if wallet_size < 0.01:
            to_remove.add(token_id)
            continue

    return to_remove


async def sync_balance(state: DashboardState) -> None:
    """Sync USDC balance. Falls back to budget setting if API fails."""
    try:
        bal = polymarket.get_balance()
        if bal > 0:
            state.metrics.balance = bal
            return
    except Exception as e:
        logger.debug(f"Balance API unavailable: {e}")

    # Fallback: use budget from config minus exposure
    from backend.config import settings
    if state.metrics.balance == 0:
        state.metrics.balance = settings.MAX_TOTAL_EXPOSURE
