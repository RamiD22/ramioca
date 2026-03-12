"""Supabase persistence layer — fire-and-forget writes, sync reads.

Provides non-blocking persistence for trades and PnL snapshots.
If SUPABASE_URL is not configured, all operations silently no-op.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from backend.config import settings
from backend.models import PortfolioMetrics, TradeRecord

logger = logging.getLogger(__name__)

# Lazy-init: only import and create client if URL is configured
_client = None


def _get_client():
    """Lazy-initialize the Supabase client."""
    global _client
    if _client is not None:
        return _client
    if not settings.SUPABASE_URL or not settings.SUPABASE_KEY:
        return None
    try:
        from supabase import create_client

        _client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)
        logger.info("Supabase client initialized")
        return _client
    except Exception as e:
        logger.error(f"Failed to init Supabase client: {e}")
        return None


def _fire_and_forget(coro) -> None:
    """Schedule a coroutine as a fire-and-forget task on the running loop."""
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(coro)
    except RuntimeError:
        logger.debug("No event loop for fire-and-forget write")


async def _safe_execute(fn):
    """Run a sync Supabase call in executor, catch all errors."""
    try:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, fn)
    except Exception as e:
        logger.warning(f"Supabase write failed (non-fatal): {e}")
        return None


# ── WRITES (fire-and-forget) ──────────────────────────────────────────


def persist_trade(trade: TradeRecord, agent_id: str) -> None:
    """Fire-and-forget: upsert a trade row."""
    client = _get_client()
    if not client:
        return

    row = {
        "id": trade.id,
        "created_at": trade.timestamp.isoformat(),
        "agent": agent_id,
        "market": trade.market,
        "side": trade.side.value if hasattr(trade.side, "value") else str(trade.side),
        "price": trade.price,
        "size": trade.size,
        "token_id": trade.token_id,
        "status": trade.status,
        "pnl": trade.pnl,
    }

    async def _insert():
        await _safe_execute(
            lambda: client.table("trades").upsert(row).execute()
        )

    _fire_and_forget(_insert())


def persist_pnl_snapshot(agent_id: str, metrics: PortfolioMetrics) -> None:
    """Fire-and-forget: insert a PnL snapshot row."""
    client = _get_client()
    if not client:
        return

    row = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "agent": agent_id,
        "total_pnl": metrics.total_pnl,
        "balance": metrics.balance,
        "total_exposure": metrics.total_exposure,
        "win_rate": metrics.win_rate,
        "total_trades": metrics.total_trades,
    }

    async def _insert():
        await _safe_execute(
            lambda: client.table("pnl_snapshots").insert(row).execute()
        )

    _fire_and_forget(_insert())


def update_trade_pnl(trade_id: str, pnl: float, status: str) -> None:
    """Fire-and-forget: update PnL and status on an existing trade."""
    client = _get_client()
    if not client:
        return

    async def _update():
        await _safe_execute(
            lambda: client.table("trades")
            .update({"pnl": pnl, "status": status})
            .eq("id", trade_id)
            .execute()
        )

    _fire_and_forget(_update())


# ── READS (synchronous, used at startup and by REST endpoint) ────────


def fetch_pnl_history(
    agent_id: str, limit: int = 500
) -> list[dict[str, Any]]:
    """Fetch recent PnL snapshots for an agent. Returns [] on error."""
    client = _get_client()
    if not client:
        return []
    try:
        resp = (
            client.table("pnl_snapshots")
            .select("created_at, total_pnl, balance, win_rate")
            .eq("agent", agent_id)
            .order("created_at", desc=False)
            .limit(limit)
            .execute()
        )
        return resp.data or []
    except Exception as e:
        logger.warning(f"Failed to fetch PnL history: {e}")
        return []


def fetch_trades_history(
    agent_id: str, limit: int = 200
) -> list[dict[str, Any]]:
    """Fetch recent trades for an agent. Returns [] on error."""
    client = _get_client()
    if not client:
        return []
    try:
        resp = (
            client.table("trades")
            .select("*")
            .eq("agent", agent_id)
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        return resp.data or []
    except Exception as e:
        logger.warning(f"Failed to fetch trade history: {e}")
        return []


def load_agent_history(agent_id: str) -> dict[str, Any]:
    """Load historical trades and ownership data for an agent on startup.

    Returns:
        {
            "trades": list[TradeRecord],
            "owned_token_ids": set[str],
            "owned_sizes": dict[str, float],
        }
    """
    from backend.models import TradeRecord as TR, Side
    from datetime import datetime, timezone

    result: dict[str, Any] = {
        "trades": [],
        "owned_token_ids": set(),
        "owned_sizes": {},
    }

    rows = fetch_trades_history(agent_id, limit=500)
    if not rows:
        logger.info(f"[{agent_id}] No historical trades found in Supabase")
        return result

    trades = []
    for row in rows:
        try:
            trade = TR(
                id=row["id"],
                timestamp=datetime.fromisoformat(row["created_at"].replace("Z", "+00:00")),
                market=row.get("market", "Unknown"),
                side=Side(row.get("side", "BUY")),
                price=float(row.get("price", 0)),
                size=float(row.get("size", 0)),
                token_id=row.get("token_id", ""),
                status=row.get("status", "filled"),
                pnl=float(row["pnl"]) if row.get("pnl") is not None else None,
                agent=agent_id,
            )
            trades.append(trade)

            # Restore ownership for active (non-settled) trades
            if trade.status not in ("settled", "cancelled") and trade.token_id:
                result["owned_token_ids"].add(trade.token_id)
                result["owned_sizes"][trade.token_id] = (
                    result["owned_sizes"].get(trade.token_id, 0) + trade.size
                )
        except Exception as e:
            logger.warning(f"[{agent_id}] Skipping malformed trade row: {e}")
            continue

    result["trades"] = trades
    logger.info(
        f"[{agent_id}] Loaded {len(trades)} historical trades from Supabase "
        f"({len(result['owned_token_ids'])} active token_ids)"
    )
    return result
