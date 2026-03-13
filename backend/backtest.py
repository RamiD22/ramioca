"""Backtest the enhanced trading strategy against historical polybacktest.com data.

Uses the PolyBackTest API (https://api.polybacktest.com/v2) for historical
Polymarket up/down market snapshots across all market types (5m, 15m, 1h, 4h,
daily), combined with Binance historical price data for signal generation.

PolyBackTest market fields:
  market_id, slug, market_type, start_time, end_time, btc_price_start,
  btc_price_end, condition_id, clob_token_up, clob_token_down, winner

PolyBackTest snapshot fields:
  id, time, market_id, btc_price, price_up, price_down

Usage:
    python -m backend.backtest [--coin btc|eth] [--market-type all] [--limit 500]
"""

from __future__ import annotations

import argparse
import json
import logging
import time
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import httpx
import numpy as np

from backend.bot.agent import StrategyState
import backend.bot.enhanced_strategy as enhanced_mod
from backend.bot.enhanced_strategy import analyze_market_enhanced
from backend.bot.risk import calculate_position_size
from backend.models import MarketInfo, Timeframe

# Suppress noisy logging from dependencies
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("backend.bot.enhanced_strategy").setLevel(logging.WARNING)
logging.getLogger("backend.bot.risk").setLevel(logging.WARNING)
logging.getLogger("backend.bot.strategy").setLevel(logging.WARNING)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# Monkey-patch order book calls — they can't work for historical backtesting
# and the retry logic makes it extremely slow (~8s per snapshot).
enhanced_mod._get_order_book_imbalance = lambda token_id: 0.0
enhanced_mod._fetch_order_book_summary = lambda token_id: ("backtest: no order book", 0.0)

POLYBACKTEST_BASE = "https://api.polybacktest.com/v2"
BINANCE_BASE = "https://api.binance.com/api/v3"

COIN_TO_SYMBOL = {"btc": "BTCUSDT", "eth": "ETHUSDT"}

DEFAULT_API_KEY = "pdm_sDxwjLzYAFhp7YQg98xeHm6l5JyaLYY5"

ALL_MARKET_TYPES = ["5m", "15m", "1hr", "4hr", "24hr"]

# Title templates that trigger the correct is_*_updown_market() detector
TITLE_TEMPLATES = {
    "5m": "{coin} Up or Down - 5m ({market_id})",
    "15m": "{coin} Up or Down - 15m ({market_id})",
    "1hr": "{coin} Up or Down - 1h ({market_id})",
    "4hr": "{coin} Up or Down - 4h ({market_id})",
    "24hr": "{coin} Up or Down - March 14, 11PM ET ({market_id})",
}

# Binance candle cache: (symbol, interval, bucket_key) -> prices
_binance_cache: dict[tuple[str, str, int], list[float]] = {}

# Bucket size for Binance cache (5 minutes in ms — nearby markets share candles)
_CACHE_BUCKET_MS = 5 * 60 * 1000


@dataclass
class BacktestTrade:
    market_id: str
    market_title: str
    market_type: str
    side: str  # BUY (Up) or SELL (Down)
    entry_price: float
    size_usdc: float
    outcome: str | None = None  # "won" | "lost"
    pnl: float = 0.0
    edge: float = 0.0
    composite_signal: str = ""
    reasoning: str = ""
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
    return {"X-API-Key": api_key} if api_key else {}


def fetch_markets(
    client: httpx.Client,
    coin: str, api_key: str | None = None, market_type: str = "5m", limit: int = 50,
) -> list[dict]:
    """Fetch historical markets from PolyBackTest with pagination."""
    PAGE_SIZE = 100  # API max per request
    all_markets: list[dict] = []
    offset = 0
    while len(all_markets) < limit:
        batch_size = min(PAGE_SIZE, limit - len(all_markets))
        resp = client.get(
            f"{POLYBACKTEST_BASE}/markets",
            params={
                "coin": coin,
                "market_type": market_type,
                "limit": str(batch_size),
                "offset": str(offset),
            },
            headers=_headers(api_key),
        )
        if resp.status_code == 401:
            logger.error("API key required or invalid.")
            return all_markets
        resp.raise_for_status()
        data = resp.json()
        batch = data.get("markets", data.get("data", [])) if isinstance(data, dict) else data
        if not batch:
            break
        all_markets.extend(batch)
        offset += len(batch)
        if len(batch) < batch_size:
            break  # no more data
    return all_markets


def fetch_snapshots(
    client: httpx.Client,
    market_id: str, coin: str, api_key: str | None = None,
    limit: int = 100, offset: int = 0,
) -> tuple[list[dict], int]:
    """Fetch price snapshots for a specific market.

    Returns (snapshots, total_count). Retries once on timeout.
    """
    for attempt in range(2):
        try:
            resp = client.get(
                f"{POLYBACKTEST_BASE}/markets/{market_id}/snapshots",
                params={
                    "coin": coin,
                    "limit": str(limit),
                    "offset": str(offset),
                    "include_orderbook": "false",
                },
                headers=_headers(api_key),
            )
            if resp.status_code in (401, 402, 403):
                return [], 0
            resp.raise_for_status()
            data = resp.json()
            total = int(data.get("total", 0)) if isinstance(data, dict) else 0
            if isinstance(data, dict):
                snaps = data.get("snapshots", data.get("data", []))
            else:
                snaps = data
            return snaps, total
        except (httpx.ReadTimeout, httpx.ConnectTimeout):
            if attempt == 0:
                continue
            return [], 0


def fetch_binance_candles(
    client: httpx.Client,
    symbol: str, interval: str, start_ms: int, end_ms: int, limit: int = 200,
) -> list[float]:
    """Fetch historical candles from Binance with caching."""
    # Bucket by start_ms so nearby markets share cached candle data
    bucket_key = start_ms // _CACHE_BUCKET_MS
    cache_key = (symbol, interval, bucket_key)
    if cache_key in _binance_cache:
        return _binance_cache[cache_key]

    for attempt in range(2):
        try:
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
            prices = [float(candle[4]) for candle in resp.json()]
            _binance_cache[cache_key] = prices
            return prices
        except (httpx.ReadTimeout, httpx.ConnectTimeout):
            if attempt == 0:
                continue
            raise


def _parse_ts(ts_str: str) -> float:
    """Parse ISO timestamp string to epoch seconds."""
    return datetime.fromisoformat(ts_str.replace("Z", "+00:00")).timestamp()


def _build_title(coin: str, market_type: str, market_id: str) -> str:
    """Build a title that triggers the correct market type detector."""
    template = TITLE_TEMPLATES.get(market_type, TITLE_TEMPLATES["5m"])
    return template.format(coin=coin.upper(), market_id=market_id)


def run_backtest(
    coin: str = "btc",
    api_key: str | None = None,
    market_type: str = "all",
    limit: int = 500,
    initial_balance: float = 200.0,
) -> BacktestResult:
    """Run the full backtest against PolyBackTest historical data."""
    api_key = api_key or DEFAULT_API_KEY
    symbol = COIN_TO_SYMBOL.get(coin, "BTCUSDT")
    result = BacktestResult()
    balance = initial_balance

    # Clear Binance cache for fresh run
    _binance_cache.clear()

    # Determine which market types to fetch
    types_to_fetch = ALL_MARKET_TYPES if market_type == "all" else [market_type]

    # Shared HTTP clients (connection pooling, generous timeouts)
    poly_client = httpx.Client(timeout=60)
    binance_client = httpx.Client(timeout=30)

    try:
        # Fetch markets across all requested types
        all_markets: list[tuple[str, dict]] = []  # (market_type, market_data)
        for mt in types_to_fetch:
            logger.info(f"Fetching {limit} {coin.upper()} {mt} markets from PolyBackTest...")
            mkts = fetch_markets(poly_client, coin, api_key, market_type=mt, limit=limit)
            logger.info(f"  Got {len(mkts)} {mt} markets")
            for m in mkts:
                all_markets.append((mt, m))

        if not all_markets:
            logger.error("No markets fetched. Check API key or connectivity.")
            return result

        total_markets = len(all_markets)
        logger.info(f"Total: {total_markets} markets across {types_to_fetch}. Running backtest...")
        t0 = time.monotonic()

        for i, (mt, mkt) in enumerate(all_markets):
            market_id = str(mkt.get("market_id", ""))
            slug = mkt.get("slug", "")
            start_time_str = mkt.get("start_time", "")
            end_time_str = mkt.get("end_time", "")
            winner = mkt.get("winner")  # "up" | "down" | null

            title = _build_title(coin, mt, market_id)

            if not start_time_str or not end_time_str:
                continue

            # Convert to epoch
            try:
                start_epoch = _parse_ts(start_time_str)
                end_epoch = _parse_ts(end_time_str)
                start_ms = int(start_epoch * 1000)
                end_ms = int(end_epoch * 1000)
            except (ValueError, TypeError):
                continue

            # Resolve winner
            if winner is None:
                btc_start = mkt.get("btc_price_start")
                btc_end = mkt.get("btc_price_end")
                if btc_start and btc_end:
                    winner = "up" if float(btc_end) > float(btc_start) else "down"
                else:
                    snaps, _ = fetch_snapshots(poly_client, market_id, coin, api_key, limit=5)
                    if snaps:
                        last = snaps[-1]
                        price_up = float(last.get("price_up") or 0.5)
                        if price_up > 0.85:
                            winner = "up"
                        elif price_up < 0.15:
                            winner = "down"

            if winner is None:
                result.skipped_no_winner += 1
                continue

            outcome = winner.lower()

            # Fetch snapshots — paginate to reach the decision window.
            # First fetch a small batch to learn total count.
            first_snaps, total_snaps = fetch_snapshots(
                poly_client, market_id, coin, api_key, limit=10, offset=0,
            )
            if not first_snaps:
                result.skipped_no_signal += 1
                continue

            # Determine timing gate start for this market type
            gate_start = {"5m": 0.35, "15m": 0.30, "1hr": 0.15, "4hr": 0.10, "24hr": 0.0}.get(mt, 0.30)
            target_offset = max(0, int(total_snaps * gate_start) - 50)  # start a bit before gate
            snaps, _ = fetch_snapshots(
                poly_client, market_id, coin, api_key, limit=1000, offset=target_offset,
            )
            if not snaps:
                result.skipped_no_signal += 1
                continue

            # Sort snapshots chronologically
            snaps.sort(key=lambda s: s.get("time", ""))

            btc_price_start = float(mkt.get("btc_price_start") or 0)
            if btc_price_start == 0 and snaps:
                btc_price_start = float(snaps[0].get("btc_price") or 0)
            if btc_price_start == 0:
                continue

            # Fetch Binance price data leading up to the market start (with caching)
            lookback_5m = 100 * 5 * 60 * 1000
            lookback_1h = 100 * 60 * 60 * 1000
            lookback_4h = 100 * 4 * 60 * 60 * 1000

            try:
                prices_5m = fetch_binance_candles(binance_client, symbol, "5m", start_ms - lookback_5m, start_ms)
                prices_1h = fetch_binance_candles(binance_client, symbol, "1h", start_ms - lookback_1h, start_ms)
                prices_4h = fetch_binance_candles(binance_client, symbol, "4h", start_ms - lookback_4h, start_ms)
            except Exception as e:
                logger.warning(f"Binance data fetch failed for {title}: {e}")
                continue

            if not prices_5m:
                continue

            price_data = {
                Timeframe.M5: prices_5m,
                Timeframe.H1: prices_1h,
                Timeframe.H4: prices_4h,
            }

            token_up = mkt.get("clob_token_up", f"{market_id}_up")
            token_down = mkt.get("clob_token_down", f"{market_id}_down")

            # Multi-snapshot simulation: sample ~20 evenly-spaced snapshots as decision points.
            # Take the first actionable signal (like the real bot with cooldown).
            if len(snaps) > 20:
                step = len(snaps) // 20
                sampled_snaps = snaps[::step]
            else:
                sampled_snaps = snaps

            traded = False
            for snap in sampled_snaps:
                snap_time_str = snap.get("time", "")
                if not snap_time_str:
                    continue

                try:
                    snap_epoch = _parse_ts(snap_time_str)
                except (ValueError, TypeError):
                    continue

                # Compute window timing
                window_duration = end_epoch - start_epoch
                if window_duration <= 0:
                    continue
                elapsed_pct = (snap_epoch - start_epoch) / window_duration
                elapsed_pct = max(0.0, min(1.0, elapsed_pct))

                # Compute window delta from this snapshot
                btc_at_snap = float(snap.get("btc_price") or 0)
                if btc_at_snap == 0:
                    continue
                window_delta = (btc_at_snap - btc_price_start) / btc_price_start

                market_price_up = float(snap.get("price_up") or 0.5)
                market_price_down = float(snap.get("price_down") or 0.5)

                # Build MarketInfo with reconstructed window context
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
                    window_delta=window_delta,
                    window_elapsed_pct=elapsed_pct,
                )

                # Run enhanced strategy
                bt_state = StrategyState()
                bt_state.last_trade_time = 0.0
                signal = analyze_market_enhanced(market_info, price_data, bt_state)

                if signal.recommended_side is None:
                    continue

                # Got an actionable signal — execute trade
                side = signal.recommended_side.value
                bet_direction = "up" if side == "BUY" else "down"
                entry_price = market_price_up if side == "BUY" else market_price_down

                # Position size
                exposure = sum(t.size_usdc for t in result.trades[-10:] if t.outcome is None)
                size = calculate_position_size(signal, balance, exposure)
                if size <= 0:
                    result.skipped_no_size += 1
                    traded = True
                    break

                # Determine PnL
                won = bet_direction == outcome
                if won:
                    pnl = size * ((1.0 / entry_price) - 1.0) if entry_price > 0 else 0
                else:
                    pnl = -size

                trade = BacktestTrade(
                    market_id=market_id,
                    market_title=title,
                    market_type=mt,
                    side=side,
                    entry_price=round(entry_price, 3),
                    size_usdc=round(size, 2),
                    outcome="won" if won else "lost",
                    pnl=round(pnl, 2),
                    edge=signal.edge,
                    composite_signal=signal.composite_signal.value,
                    reasoning=f"delta={window_delta*100:+.3f}% elapsed={elapsed_pct:.0%}",
                    timestamp=snap_time_str,
                )
                result.trades.append(trade)
                balance += pnl
                result.total_wagered += size

                logger.info(
                    f"  {'WIN' if won else 'LOSS'} [{mt:>4s}] {side} ${size:.0f} @ {entry_price:.2f} "
                    f"-> PnL ${pnl:+.2f} | edge={signal.edge:.3f} [{signal.composite_signal.value}]"
                )
                traded = True
                break  # first actionable signal, then cooldown

            if not traded:
                result.skipped_no_signal += 1

            # Progress with ETA
            if (i + 1) % 50 == 0 or (i + 1) == total_markets:
                elapsed = time.monotonic() - t0
                rate = (i + 1) / elapsed
                remaining = (total_markets - i - 1) / rate if rate > 0 else 0
                cache_hits = len(_binance_cache)
                logger.info(
                    f"  [{i + 1}/{total_markets}] trades: {len(result.trades)} | "
                    f"bal: ${balance:.2f} | "
                    f"cache: {cache_hits} | "
                    f"ETA: {remaining:.0f}s"
                )

    finally:
        poly_client.close()
        binance_client.close()

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


def save_results(
    result: BacktestResult, coin: str, market_type: str, initial_balance: float,
) -> Path:
    """Save results to a JSON file for persistence."""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    mt_label = market_type if market_type != "all" else "all"
    filename = f"backtest_{coin}_{mt_label}_{ts}.json"
    out_dir = Path(__file__).parent / "backtest_results"
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / filename

    total = result.win_count + result.loss_count
    data = {
        "meta": {
            "coin": coin,
            "market_type": market_type,
            "initial_balance": initial_balance,
            "final_balance": initial_balance + result.total_pnl,
            "timestamp": ts,
        },
        "summary": {
            "total_trades": total,
            "wins": result.win_count,
            "losses": result.loss_count,
            "win_rate": result.win_count / total if total > 0 else 0,
            "total_pnl": round(result.total_pnl, 2),
            "roi_pct": round(result.roi_pct, 2),
            "total_wagered": round(result.total_wagered, 2),
            "sharpe": round(result.sharpe, 4),
            "max_drawdown": round(result.max_drawdown, 2),
            "skipped_no_winner": result.skipped_no_winner,
            "skipped_no_signal": result.skipped_no_signal,
            "skipped_no_size": result.skipped_no_size,
        },
        "trades": [asdict(t) for t in result.trades],
    }

    out_path.write_text(json.dumps(data, indent=2))
    return out_path


def print_results(
    result: BacktestResult, coin: str, market_type: str, initial_balance: float = 200.0,
) -> None:
    """Pretty-print backtest results with per-market-type breakdown."""
    total = result.win_count + result.loss_count
    win_rate = result.win_count / total if total > 0 else 0
    final_balance = initial_balance + result.total_pnl

    mt_label = market_type.upper() if market_type != "all" else "ALL"
    print("\n" + "=" * 70)
    print(f"  BACKTEST RESULTS — {coin.upper()} {mt_label} Up/Down Markets (Enhanced Strategy)")
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

    # Per-market-type breakdown
    if result.trades:
        by_type: dict[str, list[BacktestTrade]] = defaultdict(list)
        for t in result.trades:
            by_type[t.market_type].append(t)

        if len(by_type) > 1:
            print("\n  PER-MARKET-TYPE BREAKDOWN:")
            print("-" * 70)
            for mt in ALL_MARKET_TYPES:
                trades = by_type.get(mt, [])
                if not trades:
                    continue
                wins = sum(1 for t in trades if t.outcome == "won")
                losses = sum(1 for t in trades if t.outcome == "lost")
                mt_total = wins + losses
                mt_wr = wins / mt_total if mt_total > 0 else 0
                mt_pnl = sum(t.pnl for t in trades)
                mt_wagered = sum(t.size_usdc for t in trades)
                mt_roi = (mt_pnl / mt_wagered * 100) if mt_wagered > 0 else 0
                print(
                    f"  {mt:>4s}:  {mt_total:3d} trades | "
                    f"W/L: {wins}/{losses} ({mt_wr:.0%}) | "
                    f"PnL: ${mt_pnl:+.2f} | ROI: {mt_roi:+.1f}%"
                )
            print("-" * 70)

    # Per-signal breakdown
    if result.trades:
        by_signal: dict[str, list[BacktestTrade]] = defaultdict(list)
        for t in result.trades:
            by_signal[t.composite_signal].append(t)

        print("\n  PER-SIGNAL BREAKDOWN:")
        print("-" * 70)
        for sig_name, trades in sorted(by_signal.items()):
            wins = sum(1 for t in trades if t.outcome == "won")
            losses = sum(1 for t in trades if t.outcome == "lost")
            sig_total = wins + losses
            sig_wr = wins / sig_total if sig_total > 0 else 0
            sig_pnl = sum(t.pnl for t in trades)
            print(
                f"  {sig_name:>12s}:  {sig_total:3d} trades | "
                f"W/L: {wins}/{losses} ({sig_wr:.0%}) | "
                f"PnL: ${sig_pnl:+.2f}"
            )
        print("-" * 70)

    # Trade log (last 50 trades if many)
    if result.trades:
        show_trades = result.trades if len(result.trades) <= 50 else result.trades[-50:]
        if len(result.trades) > 50:
            print(f"\n  TRADE LOG (last 50 of {len(result.trades)}):")
        else:
            print("\n  TRADE LOG:")
        print("-" * 70)
        for t in show_trades:
            icon = "+" if t.outcome == "won" else "-"
            ts_short = t.timestamp[11:16] if len(t.timestamp) > 16 else t.timestamp
            print(
                f"  {icon} {ts_short} [{t.market_type:>4s}] {t.side:4s} ${t.size_usdc:6.2f} @ {t.entry_price:.3f} "
                f"-> {t.outcome:4s} PnL: ${t.pnl:+7.2f}  "
                f"edge={t.edge:.3f} [{t.composite_signal}]"
            )
        print("-" * 70)

    # Edge analysis
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
    parser = argparse.ArgumentParser(description="Backtest enhanced crypto prediction strategy")
    parser.add_argument("--coin", default="btc", choices=["btc", "eth"])
    parser.add_argument(
        "--market-type", default="all",
        choices=["5m", "15m", "1hr", "4hr", "24hr", "all"],
        help="Market type to backtest (default: all)",
    )
    parser.add_argument("--limit", type=int, default=500, help="Number of markets per type to backtest")
    parser.add_argument("--api-key", default=None, help="PolyBackTest API key")
    parser.add_argument("--balance", type=float, default=200.0, help="Starting balance")
    args = parser.parse_args()

    result = run_backtest(
        coin=args.coin,
        api_key=args.api_key,
        market_type=args.market_type,
        limit=args.limit,
        initial_balance=args.balance,
    )
    print_results(result, args.coin, args.market_type, args.balance)

    # Save results to JSON
    out_path = save_results(result, args.coin, args.market_type, args.balance)
    logger.info(f"Results saved to {out_path}")
