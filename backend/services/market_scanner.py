"""Discover and track active crypto prediction markets on Polymarket.

Uses the Gamma events API with computed slug+timestamp to find
the currently-live 5-minute up/down markets for BTC, ETH, SOL, XRP.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx

from backend.config import settings
from backend.models import MarketInfo

logger = logging.getLogger(__name__)

_ET = ZoneInfo("America/New_York")

# Slug prefixes for the 5-minute up/down markets
_5M_SLUGS = {
    "btc-updown-5m": "BTCUSDT",
    "eth-updown-5m": "ETHUSDT",
    "sol-updown-5m": "SOLUSDT",
    "xrp-updown-5m": "XRPUSDT",
}


def _current_5m_windows() -> list[int]:
    """Return unix timestamps for the current 5-min window start time.

    5-min markets are created at :00, :05, :10, ... :55 each hour.
    The slug uses the START time's unix timestamp (e.g., btc-updown-5m-1773331800
    for the 12:10-12:15 window that starts at 12:10).
    Only returns the CURRENT window — trade the active market, not the next one.
    """
    now_et = datetime.now(_ET)
    # Current window: round down to nearest 5 minutes
    minute = now_et.minute
    window_start_min = (minute // 5) * 5
    window_start = now_et.replace(minute=window_start_min, second=0, microsecond=0)

    return [int(window_start.timestamp())]


def _parse_event_market(event_data: dict) -> MarketInfo | None:
    """Parse a MarketInfo from a Gamma events API response.

    Each 5-min event has exactly one sub-market.
    """
    markets = event_data.get("markets", [])
    if not markets:
        return None

    m = markets[0]  # Each 5-min event has one market

    clob_ids = m.get("clobTokenIds")
    if isinstance(clob_ids, str):
        try:
            clob_ids = json.loads(clob_ids)
        except (json.JSONDecodeError, TypeError):
            return None

    if not clob_ids or not isinstance(clob_ids, list) or len(clob_ids) < 2:
        return None

    prices = m.get("outcomePrices", ["0.5", "0.5"])
    if isinstance(prices, str):
        try:
            prices = json.loads(prices)
        except (json.JSONDecodeError, TypeError):
            prices = ["0.5", "0.5"]

    return MarketInfo(
        condition_id=m.get("conditionId", m.get("id", "")),
        question=m.get("question", event_data.get("title", "")),
        slug=event_data.get("slug", m.get("slug", "")),
        token_id_yes=str(clob_ids[0]),
        token_id_no=str(clob_ids[1]),
        price_yes=float(prices[0]) if prices else 0.5,
        price_no=float(prices[1]) if len(prices) > 1 else 0.5,
        volume=float(m.get("volumeNum", m.get("volume", 0))),
        liquidity=float(m.get("liquidityNum", m.get("liquidity", 0))),
        end_date=m.get("endDate"),
        category="crypto",
    )


async def fetch_live_5m_markets() -> list[MarketInfo]:
    """Fetch currently-live 5-minute up/down markets from Gamma events API.

    Constructs slugs like 'btc-updown-5m-{unix_ts}' for the current and next
    5-minute windows, then fetches each event by slug.

    Returns MarketInfo objects for all live markets found.
    """
    markets: list[MarketInfo] = []
    timestamps = _current_5m_windows()

    async with httpx.AsyncClient(timeout=15) as client:
        # Build all fetch tasks (current + next window × 4 assets = up to 8 requests)
        tasks = []
        for slug_prefix, symbol in _5M_SLUGS.items():
            for ts in timestamps:
                slug = f"{slug_prefix}-{ts}"
                tasks.append((slug, symbol, client.get(
                    f"{settings.GAMMA_HOST}/events",
                    params={"slug": slug},
                )))

        # Execute all fetches concurrently
        for slug, symbol, coro in tasks:
            try:
                resp = await coro
                if resp.status_code != 200:
                    continue

                data = resp.json()
                events = data if isinstance(data, list) else [data]

                if not events or not events[0]:
                    continue

                info = _parse_event_market(events[0])
                if info:
                    # Tag with the Binance symbol for the price feed
                    info.category = symbol  # Reuse category field for symbol mapping
                    markets.append(info)
                    logger.debug(f"Found 5M market: {info.question} ({symbol})")

            except Exception as e:
                logger.debug(f"Failed to fetch {slug}: {e}")

    now_et = datetime.now(_ET)
    logger.info(
        f"Found {len(markets)} live 5M markets at "
        f"{now_et.strftime('%I:%M %p')} ET"
    )
    return markets


# Keep the old function for backward compatibility (other market types)
CRYPTO_KEYWORDS = [
    "bitcoin", "btc", "ethereum", "eth", "solana", "sol ",
    "xrp", "dogecoin", "doge",
]


def _parse_market(m: dict) -> MarketInfo | None:
    """Parse a market from the Gamma /markets endpoint."""
    clob_ids = m.get("clobTokenIds")
    if isinstance(clob_ids, str):
        try:
            clob_ids = json.loads(clob_ids)
        except (json.JSONDecodeError, TypeError):
            return None

    if not clob_ids or not isinstance(clob_ids, list) or len(clob_ids) < 2:
        return None

    prices = m.get("outcomePrices", ["0.5", "0.5"])
    if isinstance(prices, str):
        try:
            prices = json.loads(prices)
        except (json.JSONDecodeError, TypeError):
            prices = ["0.5", "0.5"]

    return MarketInfo(
        condition_id=m.get("conditionId", m.get("id", "")),
        question=m.get("question", ""),
        slug=m.get("slug", ""),
        token_id_yes=str(clob_ids[0]),
        token_id_no=str(clob_ids[1]),
        price_yes=float(prices[0]) if prices else 0.5,
        price_no=float(prices[1]) if len(prices) > 1 else 0.5,
        volume=float(m.get("volumeNum", m.get("volume", 0))),
        liquidity=float(m.get("liquidityNum", m.get("liquidity", 0))),
        end_date=m.get("endDate"),
        category="crypto",
    )


async def fetch_crypto_markets(limit: int = 50) -> list[MarketInfo]:
    """Fetch active crypto markets from the Gamma /markets endpoint (legacy)."""
    markets: list[MarketInfo] = []
    seen: set[str] = set()

    async with httpx.AsyncClient(timeout=15) as client:
        try:
            resp = await client.get(
                f"{settings.GAMMA_HOST}/markets",
                params={
                    "active": "true",
                    "closed": "false",
                    "limit": str(min(limit * 10, 500)),
                    "order": "volume",
                    "ascending": "false",
                },
            )
            resp.raise_for_status()
            data = resp.json()
            all_markets = data if isinstance(data, list) else data.get("data", [])

            for m in all_markets:
                question = m.get("question", "").lower()
                if not any(kw in question for kw in CRYPTO_KEYWORDS):
                    continue
                info = _parse_market(m)
                if info and info.condition_id not in seen:
                    seen.add(info.condition_id)
                    markets.append(info)

        except Exception as e:
            logger.error(f"Error fetching markets: {e}")

    markets.sort(key=lambda m: m.volume, reverse=True)
    logger.info(f"Found {len(markets)} active crypto markets")
    return markets
