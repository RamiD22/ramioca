"""Polymarket CLOB client wrapper with retry logic and convenience methods."""

from __future__ import annotations

import logging
import random
import time
from typing import Any

import httpx
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import (
    ApiCreds, OrderArgs,
    BalanceAllowanceParams, AssetType,
    PartialCreateOrderOptions,
)

from backend.config import settings

logger = logging.getLogger(__name__)


def _backoff(attempt: int) -> float:
    return min(1 * (2**attempt), 60) + random.random()


class PolymarketClient:
    """Thin wrapper around py-clob-client with retry logic."""

    def __init__(self) -> None:
        self._client: ClobClient | None = None
        self._initialized = False
        self._address: str = ""

    def initialize(self) -> None:
        if not settings.PRIVATE_KEY:
            logger.warning("No private key configured — running in read-only mode")
            self._client = ClobClient(settings.CLOB_HOST, chain_id=settings.CHAIN_ID)
            self._initialized = True
            return

        self._client = ClobClient(
            settings.CLOB_HOST,
            chain_id=settings.CHAIN_ID,
            key=settings.PRIVATE_KEY,
            signature_type=settings.SIGNATURE_TYPE,
            funder=settings.FUNDER or None,
        )

        if settings.API_KEY and settings.API_SECRET and settings.API_PASSPHRASE:
            creds = ApiCreds(
                api_key=settings.API_KEY,
                api_secret=settings.API_SECRET,
                api_passphrase=settings.API_PASSPHRASE,
            )
            self._client.set_api_creds(creds)
        else:
            logger.info("Deriving API credentials from private key...")
            creds = self._client.create_or_derive_api_creds()
            self._client.set_api_creds(creds)
            logger.info("API credentials derived — save these to .env:")
            logger.info(f"  POLYMARKET_CLOB_API_KEY={creds.api_key}")
            logger.info(f"  POLYMARKET_CLOB_SECRET={creds.api_secret}")
            logger.info(f"  POLYMARKET_CLOB_PASSPHRASE={creds.api_passphrase}")

        self._address = self._client.get_address()
        self._initialized = True
        logger.info(f"Polymarket client initialized (address: {self._address})")

        # Ensure USDC allowance is set for trading
        if not settings.DRY_RUN:
            self._ensure_allowance()

    def _ensure_allowance(self) -> None:
        """Check and set USDC allowance for CLOB trading if needed."""
        try:
            params = BalanceAllowanceParams(
                asset_type=AssetType.COLLATERAL,
                signature_type=settings.SIGNATURE_TYPE,
            )
            resp = self._retry(lambda: self.client.get_balance_allowance(params))
            if isinstance(resp, dict):
                allowance = float(resp.get("allowance", 0))
                if allowance < 1e12:
                    logger.info("Setting USDC allowance for CLOB trading...")
                    self._retry(lambda: self.client.set_allowance(params))
                    logger.info("USDC allowance set successfully")
                else:
                    logger.info(f"USDC allowance already set ({allowance})")
        except Exception as e:
            logger.warning(f"Allowance check/set failed (may already be set): {e}")

    @property
    def client(self) -> ClobClient:
        if not self._client:
            raise RuntimeError("Client not initialized — call initialize() first")
        return self._client

    @property
    def address(self) -> str:
        return self._address

    def _retry(self, fn, max_retries: int = 3) -> Any:
        for attempt in range(max_retries):
            try:
                return fn()
            except Exception as e:
                err_msg = str(e).lower()
                # Don't retry non-transient order errors
                if any(phrase in err_msg for phrase in [
                    "crosses the book", "duplicated", "not enough balance",
                    "minimum", "invalid order", "invalid amounts",
                ]):
                    raise
                if attempt == max_retries - 1:
                    raise
                wait = _backoff(attempt)
                logger.warning(f"Retry {attempt + 1}/{max_retries} after error: {e} (waiting {wait:.1f}s)")
                time.sleep(wait)

    # -- Market data --

    def get_order_book(self, token_id: str) -> dict:
        return self._retry(lambda: self.client.get_order_book(token_id))

    def get_price(self, token_id: str, side: str = "BUY") -> float:
        resp = self._retry(lambda: self.client.get_price(token_id, side))
        return float(resp) if resp else 0.0

    def get_midpoint(self, token_id: str) -> float:
        resp = self._retry(lambda: self.client.get_midpoint(token_id))
        return float(resp) if resp else 0.0

    def get_tick_size(self, token_id: str) -> float:
        resp = self._retry(lambda: self.client.get_tick_size(token_id))
        return float(resp) if resp else 0.01

    # -- Trading --

    def _round_to_tick(self, price: float, token_id: str) -> float:
        """Round price to the nearest valid tick size for this market."""
        try:
            tick = self.get_tick_size(token_id)
        except Exception:
            tick = 0.01
        if tick <= 0:
            tick = 0.01
        return round(round(price / tick) * tick, 4)

    def place_limit_order(
        self,
        token_id: str,
        price: float,
        size: float,
        side: str,
    ) -> dict | None:
        # Snap price to valid tick
        price = self._round_to_tick(price, token_id)
        size = round(size, 2)

        if settings.DRY_RUN:
            logger.info(f"[DRY RUN] {side} {size} @ {price} on {token_id[:16]}...")
            return {"dry_run": True, "side": side, "price": price, "size": size}

        order_args = OrderArgs(
            token_id=token_id,
            price=price,
            size=size,
            side=side,
        )
        # Get tick size as string for PartialCreateOrderOptions
        try:
            tick = self.get_tick_size(token_id)
            tick_str = f"{tick:.4f}".rstrip("0").rstrip(".")
            # Must be one of: '0.1', '0.01', '0.001', '0.0001'
            valid_ticks = {"0.1", "0.01", "0.001", "0.0001"}
            if tick_str not in valid_ticks:
                tick_str = "0.01"  # safe default
        except Exception:
            tick_str = "0.01"

        options = PartialCreateOrderOptions(tick_size=tick_str)
        signed = self._retry(lambda: self.client.create_order(order_args, options))
        resp = self._retry(lambda: self.client.post_order(signed))
        logger.info(f"Order placed: {side} {size} @ {price} — response: {resp}")
        return resp

    def place_market_order(
        self,
        token_id: str,
        amount: float,
        side: str,
    ) -> dict | None:
        """Place an order that fills immediately using an aggressive limit order.

        py_clob_client's MarketOrderArgs has a precision bug: it computes taker
        amounts with up to 4 decimals, but the API allows max 2 for buy orders.
        Aggressive limit orders avoid this by using size (shares) as the input,
        which the library correctly rounds to 2 decimals.
        """
        amount = round(amount)

        if settings.DRY_RUN:
            logger.info(f"[DRY RUN] MARKET {side} ${amount} on {token_id[:16]}...")
            return {"dry_run": True, "side": side, "amount": amount}

        # Get current best price from book (use wrapper methods that parse the response)
        best_price = self.get_price(token_id, side)
        if not best_price or best_price <= 0:
            best_price = self.get_midpoint(token_id)
        if not best_price or best_price <= 0:
            raise Exception(f"Cannot get price for {token_id[:16]}…")

        # Aggressive price: cross the spread to ensure immediate fill
        if side == "BUY":
            aggressive_price = min(best_price + 0.04, 0.99)
        else:
            aggressive_price = max(best_price - 0.04, 0.01)

        # Convert USDC amount to shares (integer shares for clean rounding)
        shares = int(amount / aggressive_price)
        if shares < 1:
            shares = 1

        logger.info(
            f"Aggressive limit: {side} {shares} shares @ {aggressive_price:.4f} "
            f"(~${shares * aggressive_price:.2f}) on {token_id[:16]}…"
        )

        return self.place_limit_order(
            token_id=token_id,
            price=aggressive_price,
            size=float(shares),
            side=side,
        )

    def cancel_order(self, order_id: str) -> dict:
        return self._retry(lambda: self.client.cancel(order_id))

    def cancel_all(self) -> dict:
        return self._retry(lambda: self.client.cancel_all())

    def get_open_orders(self) -> list[dict]:
        resp = self._retry(lambda: self.client.get_orders())
        if isinstance(resp, list):
            return resp
        return resp.get("data", []) if isinstance(resp, dict) else []

    # -- Balance & Positions (via Data API + balance_allowance) --

    def get_balance(self) -> float:
        """Get USDC balance via get_balance_allowance."""
        try:
            params = BalanceAllowanceParams(
                asset_type=AssetType.COLLATERAL,
                signature_type=settings.SIGNATURE_TYPE,
            )
            resp = self._retry(lambda: self.client.get_balance_allowance(params))
            if isinstance(resp, dict):
                bal = resp.get("balance", 0)
                return float(bal) / 1e6 if float(bal) > 1000 else float(bal)
            return 0.0
        except Exception as e:
            logger.error(f"Balance fetch failed: {e}")
            return 0.0

    def get_positions(self) -> list[dict]:
        """Fetch positions from the Data API."""
        if not self._address and not settings.FUNDER:
            return []

        user = settings.FUNDER or self._address
        try:
            with httpx.Client(timeout=15) as http:
                resp = http.get(
                    f"{settings.DATA_HOST}/positions",
                    params={"user": user, "sizeThreshold": "0.01", "limit": "500"},
                )
                resp.raise_for_status()
                data = resp.json()
                return data if isinstance(data, list) else data.get("data", [])
        except Exception as e:
            logger.error(f"Positions fetch failed: {e}")
            return []

    def get_trades(self) -> list[dict]:
        """Fetch recent trades from the Data API."""
        if not self._address and not settings.FUNDER:
            return []

        user = settings.FUNDER or self._address
        try:
            with httpx.Client(timeout=15) as http:
                resp = http.get(
                    f"{settings.DATA_HOST}/trades",
                    params={"user": user, "limit": "50"},
                )
                resp.raise_for_status()
                data = resp.json()
                return data if isinstance(data, list) else data.get("data", [])
        except Exception as e:
            logger.error(f"Trades fetch failed: {e}")
            return []


polymarket = PolymarketClient()
