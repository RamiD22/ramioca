"""Real-time crypto price feeds from Binance for signal generation."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import defaultdict

import httpx
import websockets

from backend.models import Timeframe

logger = logging.getLogger(__name__)

BINANCE_WS = "wss://stream.binance.com:9443/ws"
BINANCE_REST = "https://api.binance.com/api/v3"

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT"]

KLINE_INTERVALS = {
    Timeframe.M5: "5m",
    Timeframe.H1: "1h",
    Timeframe.H4: "4h",
}


class PriceFeed:
    """Manages real-time crypto price data from Binance."""

    def __init__(self) -> None:
        # {symbol: {timeframe: [close_prices]}}
        self.candles: dict[str, dict[Timeframe, list[float]]] = defaultdict(
            lambda: {tf: [] for tf in Timeframe}
        )
        self.current_prices: dict[str, float] = {}
        self._ws_task: asyncio.Task | None = None
        self._running = False

        # Window open price tracking for 5-min markets
        self._window_opens: dict[str, float] = {}  # symbol → price at window open
        self._current_window_ts: int = 0  # unix timestamp of current 5m window start

        # 15-min window tracking
        self._window_opens_15m: dict[str, float] = {}
        self._current_window_ts_15m: int = 0

        # 1-hour window tracking
        self._window_opens_1h: dict[str, float] = {}
        self._current_window_ts_1h: int = 0

        # 4-hour window tracking
        self._window_opens_4h: dict[str, float] = {}
        self._current_window_ts_4h: int = 0

        # Daily window tracking (price at midnight ET)
        self._daily_opens: dict[str, float] = {}
        self._current_day: str = ""  # "YYYY-MM-DD"

    async def load_historical(self, symbol: str = "BTCUSDT", limit: int = 100) -> None:
        """Load historical candles from Binance REST API."""
        async with httpx.AsyncClient(timeout=15) as client:
            for tf, interval in KLINE_INTERVALS.items():
                try:
                    resp = await client.get(
                        f"{BINANCE_REST}/klines",
                        params={
                            "symbol": symbol,
                            "interval": interval,
                            "limit": limit,
                        },
                    )
                    resp.raise_for_status()
                    klines = resp.json()
                    closes = [float(k[4]) for k in klines]  # close price = index 4
                    self.candles[symbol][tf] = closes
                    if tf == Timeframe.M5 and closes:
                        self.current_prices[symbol] = closes[-1]
                    logger.info(f"Loaded {len(closes)} {interval} candles for {symbol}")
                except Exception as e:
                    logger.error(f"Failed to load {interval} candles for {symbol}: {e}")

    async def load_all_historical(self) -> None:
        """Load historical data for all tracked symbols."""
        tasks = [self.load_historical(sym) for sym in SYMBOLS]
        await asyncio.gather(*tasks)

        # Seed window opens from loaded data so window delta works from first cycle
        now = time.time()
        self._current_window_ts = int(now // 300) * 300
        self._current_window_ts_15m = int(now // 900) * 900
        self._current_window_ts_1h = int(now // 3600) * 3600
        self._current_window_ts_4h = int(now // 14400) * 14400
        from datetime import datetime
        from zoneinfo import ZoneInfo
        _et = ZoneInfo("America/New_York")
        self._current_day = datetime.now(_et).strftime("%Y-%m-%d")
        for symbol in SYMBOLS:
            if symbol in self.current_prices:
                self._window_opens[symbol] = self.current_prices[symbol]
                self._window_opens_15m[symbol] = self.current_prices[symbol]
                self._window_opens_1h[symbol] = self.current_prices[symbol]
                self._window_opens_4h[symbol] = self.current_prices[symbol]
                self._daily_opens[symbol] = self.current_prices[symbol]
                logger.info(f"Seeded window open for {symbol}: ${self.current_prices[symbol]:.2f}")

    def _check_window_boundary(self, symbol: str, price: float) -> None:
        """Track price at 5-min, 15-min, and daily window boundaries."""
        now = time.time()

        # ── 5-min boundary ──
        window_ts = int(now // 300) * 300
        if window_ts != self._current_window_ts:
            self._current_window_ts = window_ts
            for sym in SYMBOLS:
                if sym in self.current_prices:
                    self._window_opens[sym] = self.current_prices[sym]
            logger.info(
                f"New 5-min window at {window_ts}. "
                f"Opens: {', '.join(f'{s}=${p:.2f}' for s, p in self._window_opens.items())}"
            )

        # ── 15-min boundary ──
        window_ts_15m = int(now // 900) * 900
        if window_ts_15m != self._current_window_ts_15m:
            self._current_window_ts_15m = window_ts_15m
            for sym in SYMBOLS:
                if sym in self.current_prices:
                    self._window_opens_15m[sym] = self.current_prices[sym]
            logger.info(f"New 15-min window at {window_ts_15m}")

        # ── 1-hour boundary ──
        window_ts_1h = int(now // 3600) * 3600
        if window_ts_1h != self._current_window_ts_1h:
            self._current_window_ts_1h = window_ts_1h
            for sym in SYMBOLS:
                if sym in self.current_prices:
                    self._window_opens_1h[sym] = self.current_prices[sym]
            logger.info(f"New 1-hour window at {window_ts_1h}")

        # ── 4-hour boundary ──
        window_ts_4h = int(now // 14400) * 14400
        if window_ts_4h != self._current_window_ts_4h:
            self._current_window_ts_4h = window_ts_4h
            for sym in SYMBOLS:
                if sym in self.current_prices:
                    self._window_opens_4h[sym] = self.current_prices[sym]
            logger.info(f"New 4-hour window at {window_ts_4h}")

        # ── Daily boundary (midnight ET) ──
        from datetime import datetime
        from zoneinfo import ZoneInfo
        today = datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")
        if today != self._current_day:
            self._current_day = today
            for sym in SYMBOLS:
                if sym in self.current_prices:
                    self._daily_opens[sym] = self.current_prices[sym]
            logger.info(f"New trading day: {today}")

        # Set initial opens if missing
        if symbol not in self._window_opens and price > 0:
            self._window_opens[symbol] = price
        if symbol not in self._window_opens_15m and price > 0:
            self._window_opens_15m[symbol] = price
        if symbol not in self._window_opens_1h and price > 0:
            self._window_opens_1h[symbol] = price
        if symbol not in self._window_opens_4h and price > 0:
            self._window_opens_4h[symbol] = price
        if symbol not in self._daily_opens and price > 0:
            self._daily_opens[symbol] = price

    async def start_ws(self) -> None:
        """Connect to Binance WebSocket for real-time price updates."""
        self._running = True
        streams = [f"{s.lower()}@kline_1m" for s in SYMBOLS]
        url = f"{BINANCE_WS}/{'/'.join(streams)}"

        while self._running:
            try:
                async with websockets.connect(url) as ws:
                    logger.info("Connected to Binance WebSocket")
                    async for msg in ws:
                        if not self._running:
                            break
                        try:
                            data = json.loads(msg)
                            kline = data.get("k", {})
                            symbol = kline.get("s", "")
                            close = float(kline.get("c", 0))
                            if symbol and close:
                                self.current_prices[symbol] = close
                                # Track 5-minute window boundaries
                                self._check_window_boundary(symbol, close)
                                # Only append to M5 on 1m kline close (approximate —
                                # the REST API provides proper 5m candles, WS provides
                                # 1m granularity for live price updates)
                                if kline.get("x"):  # 1m candle closed
                                    self.candles[symbol][Timeframe.M5].append(close)
                                    self.candles[symbol][Timeframe.M5] = self.candles[symbol][Timeframe.M5][-200:]
                        except (json.JSONDecodeError, KeyError, ValueError):
                            continue
            except Exception as e:
                if self._running:
                    logger.error(f"WebSocket error: {e} — reconnecting in 5s")
                    await asyncio.sleep(5)

    def stop(self) -> None:
        self._running = False

    def get_prices(self, symbol: str) -> dict[Timeframe, list[float]]:
        """Get all timeframe price data for a symbol."""
        return dict(self.candles.get(symbol, {tf: [] for tf in Timeframe}))

    def get_current_price(self, symbol: str) -> float:
        return self.current_prices.get(symbol, 0.0)

    def get_window_delta(self, symbol: str) -> float:
        """Get price change from the current 5-min window open to now."""
        current = self.current_prices.get(symbol, 0.0)
        window_open = self._window_opens.get(symbol, 0.0)
        if window_open <= 0 or current <= 0:
            return 0.0
        return (current - window_open) / window_open

    def get_window_delta_15m(self, symbol: str) -> float:
        """Get price change from the current 15-min window open to now."""
        current = self.current_prices.get(symbol, 0.0)
        window_open = self._window_opens_15m.get(symbol, 0.0)
        if window_open <= 0 or current <= 0:
            return 0.0
        return (current - window_open) / window_open

    def get_window_delta_1h(self, symbol: str) -> float:
        """Get price change from the current 1-hour window open to now."""
        current = self.current_prices.get(symbol, 0.0)
        window_open = self._window_opens_1h.get(symbol, 0.0)
        if window_open <= 0 or current <= 0:
            return 0.0
        return (current - window_open) / window_open

    def get_window_delta_4h(self, symbol: str) -> float:
        """Get price change from the current 4-hour window open to now."""
        current = self.current_prices.get(symbol, 0.0)
        window_open = self._window_opens_4h.get(symbol, 0.0)
        if window_open <= 0 or current <= 0:
            return 0.0
        return (current - window_open) / window_open

    def get_window_delta_daily(self, symbol: str) -> float:
        """Get price change from today's open (midnight ET) to now."""
        current = self.current_prices.get(symbol, 0.0)
        day_open = self._daily_opens.get(symbol, 0.0)
        if day_open <= 0 or current <= 0:
            return 0.0
        return (current - day_open) / day_open


price_feed = PriceFeed()
