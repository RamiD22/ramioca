"""Backtest the trading strategy against historical polybacktest.com data.

Uses the PolyBackTest API (https://api.polybacktest.com/v2) for historical
Polymarket 5m up/down market snapshots, combined with Binance historical
price data for signal generation.

PolyBackTest market fields:
  market_id, slug, market_type, start_time, end_time, btc_price_start,
  btc_price_end, condition_id, clob_token_up, clob_token_down, winner

PolyBackTest snapshot fields:
  id, time, market_id, btc_price, price_up, price_down

Usage:
    python -m backend.backtest [--coin btc|eth] [--limit 50] [--api-key KEY]
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx
import numpy as np

import backend.bot.strategy as strategy_mod
from backend.bot.agent import StrategyState
from backend.bot.strategy import analyze_market, is_5m_updown_market
from backend.bot.risk import calculate_position_size
from backend.models import MarketInfo, Timeframe

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

POLYBACKTEST_BASE = "https://api.polybacktest.com/v2"
BINANCE_BASE = "https://api.binance.com/api/v3"

COIN_TO_SYMBOL = {"btc": "BTCUSDT", "eth": "ETHUSDT"}


@dataclass
class BacktestTrade:
    market_id: str
    market_title: str
    side: str  # BUY (Up) or SELL (Down)
    entry_price: float
    size_usdc: float
    outcome: str | None = None  # "won" | "lost"
    pnl: float = 0.0
    edge: float = 0.0
    composite_signal: str = ""
    timestamp: str = ""


@dataclass
class BacktestResult:
    trades: list[BacktestTrade] = field(default_factory=list)
    total_pnl: float = 0.0
    win_count: int = 0
    loss_count: int = 0
    total_wagered: float = 0.0
    roi_pct: float = 0.0
    max_drawdown: float = 0.0
    sharpe: float = 0.0
    skipped_no_winner: int = 0
    skipped_no_signal: int = 0
    skipped_no_size: int = 0


def _headers(api_key: str | None) -> dict:
    return {"Authorization": f"Bearer {api_key}"} if api_key else {}


def fetch_markets(coin: str, api_key: str | None = None, limit: int = 50) -> list[dict]:
    """Fetch historical 5m markets from PolyBackTest."""
    with httpx.Client(timeout=30) as client:
        resp = client.get(
            f"{POLYBACKTEST_BASE}/markets",
            params={"coin": coin, "market_type": "5m", "limit": str(limit)},
            headers=_headers(api_key),
        )
        if resp.status_code == 401:
            logger.error("API key required or invalid.")
            return []
        resp.raise_for_status()
        data = resp.json()
        # Response: {"markets": [...], "total": N, "limit": N, "offset": N}
        if isinstance(data, dict):
            return data.get("markets", data.get("data", []))
        return data


def fetch_snapshots(
    market_id: str, coin: str, api_key: str | None = None, limit: int = 10
) -> list[dict]:
    """Fetch price snapshots for a specific market."""
    with httpx.Client(timeout=30) as client:
        resp = client.get(
            f"{POLYBACKTEST_BASE}/markets/{market_id}/snapshots",
            params={"coin": coin, "limit": str(limit), "include_orderbook": "false"},
            headers=_headers(api_key),
        )
        if resp.status_code in (401, 402, 403):
            return []
        resp.raise_for_status()
        data = resp.json()
        # Response: {"market": {...}, "snapshots": [...], ...}
        if isinstance(data, dict):
            return data.get("snapshots", data.get("data", []))
        return data


def fetch_binance_candles(
    symbol: str, interval: str, start_ms: int, end_ms: int, limit: int = 200
) -> list[float]:
    """Fetch historical candles from Binance and return close prices."""
    with httpx.Client(timeout=15) as client:
        resp = client.get(
            f"{BINANCE_BASE}/klines",
            params={
                "symbol": symbol,
                "interval": interval,
                "startTime": str(start_ms),
                "endTime": str(end_ms),
                "limit": str(limit),
            },
        )
        resp.raise_for_status()
        return [float(candle[4]) for candle in resp.json()]


def run_backtest(
    coin: str = "btc",
    api_key: str | None = None,
    limit: int = 50,
    initial_balance: float = 200.0,
) -> BacktestResult:
    """Run the full backtest against PolyBackTest historical data."""
    symbol = COIN_TO_SYMBOL.get(coin, "BTCUSDT")
    result = BacktestResult()
    balance = initial_balance

    logger.info(f"Fetching {limit} {coin.upper()} 5m markets from PolyBackTest...")
    markets = fetch_markets(coin, api_key, limit)
    if not markets:
        logger.error("No markets fetched. Check API key or connectivity.")
        return result

    logger.info(f"Fetched {len(markets)} markets. Running backtest...")

    for i, mkt in enumerate(markets):
        market_id = str(mkt.get("market_id", ""))
        slug = mkt.get("slug", "")
        start_time_str = mkt.get("start_time", "")
        end_time_str = mkt.get("end_time", "")
        winner = mkt.get("winner")  # "up" | "down" | null

        # Build a human-readable title from slug
        # e.g., "btc-updown-5m-1773315900" → "BTC Up or Down - 5m"
        title = slug.replace("-", " ").title() if slug else f"Market {market_id}"
        # Make it match our is_5m_updown_market detector
        title = f"BTC Up or Down - 5m ({market_id})"

        if not start_time_str or not end_time_str:
            continue

        # Convert to epoch ms
        try:
            start_ms = int(datetime.fromisoformat(start_time_str.replace("Z", "+00:00")).timestamp() * 1000)
            end_ms = int(datetime.fromisoformat(end_time_str.replace("Z", "+00:00")).timestamp() * 1000)
        except (ValueError, TypeError):
            continue

        # Check if market has resolved
        if winner is None:
            # Try to determine from btc_price_start vs btc_price_end
            btc_start = mkt.get("btc_price_start")
            btc_end = mkt.get("btc_price_end")
            if btc_start and btc_end:
                winner = "up" if float(btc_end) > float(btc_start) else "down"
            else:
                # Try snapshots — last snapshot price_up near 0 or 1 means resolved
                snaps = fetch_snapshots(market_id, coin, api_key, limit=5)
                if snaps:
                    last = snaps[-1]
                    price_up = float(last.get("price_up", 0.5))
                    if price_up > 0.85:
                        winner = "up"
                    elif price_up < 0.15:
                        winner = "down"
                time.sleep(0.05)

        if winner is None:
            result.skipped_no_winner += 1
            continue

        outcome = winner.lower()  # "up" or "down"

        # Get opening market price from first snapshot
        snaps = fetch_snapshots(market_id, coin, api_key, limit=3)
        if snaps:
            first_snap = snaps[0]
            market_price_up = float(first_snap.get("price_up", 0.5))
            market_price_down = float(first_snap.get("price_down", 0.5))
        else:
            market_price_up = 0.50
            market_price_down = 0.50

        # Fetch Binance price data leading up to the market start
        lookback_5m = 100 * 5 * 60 * 1000
        lookback_1h = 100 * 60 * 60 * 1000
        lookback_4h = 100 * 4 * 60 * 60 * 1000

        try:
            prices_5m = fetch_binance_candles(symbol, "5m", start_ms - lookback_5m, start_ms)
            prices_1h = fetch_binance_candles(symbol, "1h", start_ms - lookback_1h, start_ms)
            prices_4h = fetch_binance_candles(symbol, "4h", start_ms - lookback_4h, start_ms)
        except Exception as e:
            logger.warning(f"Binance data fetch failed for {title}: {e}")
            continue

        if not prices_5m:
            continue

        # Build MarketInfo
        token_up = mkt.get("clob_token_up", f"{market_id}_up")
        token_down = mkt.get("clob_token_down", f"{market_id}_down")

        market_info = MarketInfo(
            condition_id=mkt.get("condition_id", market_id),
            question=title,
            slug=slug,
            token_id_yes=token_up,
            token_id_no=token_down,
            price_yes=market_price_up,
            price_no=market_price_down,
            volume=float(mkt.get("final_volume", 0) or 0),
            liquidity=float(mkt.get("final_liquidity", 0) or 0),
            end_date=end_time_str,
        )

        # Run strategy — reset cooldown for each independent market
        bt_state = StrategyState()
        bt_state.last_trade_time = 0.0
        price_data = {
            Timeframe.M5: prices_5m,
            Timeframe.H1: prices_1h,
            Timeframe.H4: prices_4h,
        }
        signal = analyze_market(market_info, price_data, bt_state)

        if signal.recommended_side is None:
            result.skipped_no_signal += 1
            logger.debug(
                f"  SKIP {title[:40]} | composite={signal.composite_signal.value} "
                f"edge={signal.edge:.4f} prob={signal.probability_estimate:.3f} "
                f"mkt={signal.market_price:.3f}"
            )
            # Progress
            if (i + 1) % 10 == 0:
                logger.info(f"  Processed {i + 1}/{len(markets)} | trades: {len(result.trades)}")
            time.sleep(0.1)
            continue

        side = signal.recommended_side.value  # "BUY" or "SELL"

        # BUY = betting Up (buying YES token at market_price_up)
        # SELL = betting Down (buying NO token at market_price_down)
        bet_direction = "up" if side == "BUY" else "down"
        entry_price = market_price_up if side == "BUY" else market_price_down

        # Position size
        exposure = sum(t.size_usdc for t in result.trades[-10:] if t.outcome is None)
        size = calculate_position_size(signal, balance, exposure)
        if size <= 0:
            result.skipped_no_size += 1
            time.sleep(0.1)
            continue

        # Determine PnL
        won = bet_direction == outcome
        if won:
            # Token pays $1, we bought at entry_price
            # PnL = (1.0 - entry_price) / entry_price * size
            pnl = size * ((1.0 / entry_price) - 1.0) if entry_price > 0 else 0
        else:
            pnl = -size  # lose entire position

        trade = BacktestTrade(
            market_id=market_id,
            market_title=title,
            side=side,
            entry_price=round(entry_price, 3),
            size_usdc=round(size, 2),
            outcome="won" if won else "lost",
            pnl=round(pnl, 2),
            edge=signal.edge,
            composite_signal=signal.composite_signal.value,
            timestamp=start_time_str,
        )
        result.trades.append(trade)
        balance += pnl
        result.total_wagered += size

        logger.info(
            f"  {'WIN' if won else 'LOSS'} {side} ${size:.0f} @ {entry_price:.2f} "
            f"→ PnL ${pnl:+.2f} | edge={signal.edge:.3f} [{signal.composite_signal.value}]"
        )

        # Progress
        if (i + 1) % 10 == 0:
            logger.info(f"  Processed {i + 1}/{len(markets)} | trades: {len(result.trades)} | bal: ${balance:.2f}")

        time.sleep(0.1)

    # Compute final metrics
    for t in result.trades:
        result.total_pnl += t.pnl
        if t.outcome == "won":
            result.win_count += 1
        elif t.outcome == "lost":
            result.loss_count += 1

    if result.total_wagered > 0:
        result.roi_pct = (result.total_pnl / result.total_wagered) * 100

    pnls = [t.pnl for t in result.trades]
    if len(pnls) > 1:
        result.sharpe = float(np.mean(pnls) / np.std(pnls)) if np.std(pnls) > 0 else 0
        cum = np.cumsum(pnls)
        peak = np.maximum.accumulate(cum)
        dd = peak - cum
        result.max_drawdown = float(np.max(dd)) if len(dd) > 0 else 0

    return result


def print_results(result: BacktestResult, initial_balance: float = 200.0) -> None:
    """Pretty-print backtest results."""
    total = result.win_count + result.loss_count
    win_rate = result.win_count / total if total > 0 else 0
    final_balance = initial_balance + result.total_pnl

    print("\n" + "=" * 70)
    print("  BACKTEST RESULTS — BTC 5m Up/Down Markets")
    print("=" * 70)
    print(f"  Starting Balance:  ${initial_balance:.2f}")
    print(f"  Final Balance:     ${final_balance:.2f}")
    print(f"  Total PnL:         ${result.total_pnl:+.2f}")
    print(f"  ROI:               {result.roi_pct:+.1f}%")
    print(f"  Total Wagered:     ${result.total_wagered:.2f}")
    print()
    print(f"  Total Trades:      {total}")
    print(f"  Wins:              {result.win_count}  ({win_rate:.1%})")
    print(f"  Losses:            {result.loss_count}  ({1 - win_rate:.1%})")
    print(f"  Sharpe Ratio:      {result.sharpe:.3f}")
    print(f"  Max Drawdown:      ${result.max_drawdown:.2f}")
    print()
    print(f"  Skipped (no winner):  {result.skipped_no_winner}")
    print(f"  Skipped (no signal):  {result.skipped_no_signal}")
    print(f"  Skipped (no size):    {result.skipped_no_size}")
    print("=" * 70)

    if result.trades:
        print("\n  TRADE LOG:")
        print("-" * 70)
        for t in result.trades:
            icon = "✅" if t.outcome == "won" else "❌"
            ts_short = t.timestamp[11:16] if len(t.timestamp) > 16 else t.timestamp
            print(
                f"  {icon} {ts_short} {t.side:4s} ${t.size_usdc:6.2f} @ {t.entry_price:.3f} "
                f"→ {t.outcome:4s} PnL: ${t.pnl:+7.2f}  "
                f"edge={t.edge:.3f} [{t.composite_signal}]"
            )
        print("-" * 70)

    if result.trades:
        edges = [t.edge for t in result.trades]
        winning_edges = [t.edge for t in result.trades if t.outcome == "won"]
        losing_edges = [t.edge for t in result.trades if t.outcome == "lost"]
        print(f"\n  EDGE ANALYSIS:")
        print(f"  Avg Edge (all):      {np.mean(edges):.4f}")
        if winning_edges:
            print(f"  Avg Edge (winners):  {np.mean(winning_edges):.4f}")
        if losing_edges:
            print(f"  Avg Edge (losers):   {np.mean(losing_edges):.4f}")
        print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backtest crypto prediction strategy")
    parser.add_argument("--coin", default="btc", choices=["btc", "eth"])
    parser.add_argument("--limit", type=int, default=50, help="Number of markets to backtest")
    parser.add_argument("--api-key", default=None, help="PolyBackTest API key")
    parser.add_argument("--balance", type=float, default=200.0, help="Starting balance")
    args = parser.parse_args()

    result = run_backtest(
        coin=args.coin,
        api_key=args.api_key,
        limit=args.limit,
        initial_balance=args.balance,
    )
    print_results(result, args.balance)
