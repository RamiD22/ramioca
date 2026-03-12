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


def compute_metrics(
    state: DashboardState,
    pnl_baseline: float | None = None,
) -> tuple[PortfolioMetrics, float]:
    """Compute portfolio metrics from current state.

    Returns (metrics, pnl_baseline) — the baseline is set on first call
    so the dashboard starts from $0 PnL.
    """
    trades = state.recent_trades
    positions = state.positions

    # Total PnL from trade outcomes — persists after 5-min markets settle.
    # This replaces the old `sum(p.pnl for p in positions)` which reset to $0
    # every time settled positions disappeared from the Polymarket API.
    # Trade PnLs are kept up-to-date by run_agent_cycle():
    #   - Active positions: trade.pnl = proportional position PnL (unrealized)
    #   - Settled positions: trade.pnl retains last known value (realized)
    total_pnl = sum(t.pnl for t in trades if t.pnl is not None)

    # Baseline: always 0 so cumulative PnL persists across restarts.
    # Trade history (with PnLs) is loaded from Supabase on startup,
    # so total_pnl already reflects all historical gains/losses.
    if pnl_baseline is None:
        pnl_baseline = 0.0
        logger.info(f"PnL baseline set: $0.00 (persistent mode — total_pnl=${total_pnl:.2f})")

    adjusted_pnl = total_pnl - pnl_baseline
    total_exposure = sum(p.size * p.current_price for p in positions)

    winning = [t for t in trades if t.pnl is not None and t.pnl > 0]
    losing = [t for t in trades if t.pnl is not None and t.pnl < 0]
    resolved = len(winning) + len(losing)
    win_rate = len(winning) / resolved if resolved > 0 else 0.0

    # Simple Sharpe approximation from trade returns
    returns = [t.pnl / t.size if t.size > 0 else 0 for t in trades if t.pnl is not None]
    if len(returns) > 1:
        sharpe = float(np.mean(returns) / np.std(returns)) if np.std(returns) > 0 else 0.0
    else:
        sharpe = 0.0

    # Max drawdown from cumulative PnL
    cum_pnl = np.cumsum([t.pnl or 0 for t in reversed(trades)]) if trades else np.array([0])
    peak = np.maximum.accumulate(cum_pnl) if len(cum_pnl) > 0 else np.array([0])
    drawdowns = peak - cum_pnl
    max_dd = float(np.max(drawdowns)) if len(drawdowns) > 0 else 0.0

    avg_pnl = float(np.mean([t.pnl for t in trades if t.pnl is not None])) if trades else 0.0

    balance = state.metrics.balance or 0.0

    metrics = PortfolioMetrics(
        balance=_safe(balance),
        total_pnl=_safe(round(adjusted_pnl, 2)),
        total_pnl_pct=_safe(round((adjusted_pnl / balance) * 100, 2)) if balance > 0 else 0.0,
        win_rate=_safe(round(win_rate, 4)),
        total_trades=len(trades),
        winning_trades=len(winning),
        losing_trades=len(losing),
        active_positions=sum(1 for p in positions if p.current_price > 0),
        total_exposure=_safe(round(total_exposure, 2)),
        sharpe_ratio=_safe(round(sharpe, 3)),
        max_drawdown=_safe(round(max_dd, 2)),
        avg_trade_pnl=_safe(round(avg_pnl, 2)),
    )

    return metrics, pnl_baseline


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
