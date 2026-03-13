"""Claude trading strategy for 5-minute crypto binary markets.

Calls Claude every 30 seconds with full Binance data + trade history feedback.
Reads skills.md for domain knowledge. Adjusts based on recent outcomes.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

from backend.bot.client import polymarket
from backend.bot.strategy import (
    compute_adx,
    compute_bollinger,
    compute_ema,
    compute_roc,
    compute_rsi,
    compute_sma,
    compute_momentum,
    compute_volatility,
    is_5m_updown_market,
)
from backend.config import settings
from backend.models import (
    MarketInfo,
    Side,
    Signal,
    StrategyOutput,
    Timeframe,
    TimeframeSignal,
    TradeRecord,
)

if TYPE_CHECKING:
    from backend.bot.agent import StrategyState

logger = logging.getLogger(__name__)

# ── Load skills.md ──────────────────────────────────────────────

_SKILLS_PATH = Path(__file__).parent / "skills.md"
try:
    SKILLS_KNOWLEDGE = _SKILLS_PATH.read_text()
except FileNotFoundError:
    SKILLS_KNOWLEDGE = ""
    logger.warning("skills.md not found — running without knowledge base")

# ── System prompt ────────────────────────────────────────────────

SYSTEM_PROMPT = f"""You are a highly selective crypto trading agent for 5-minute binary prediction markets on Polymarket.

You are called every 30 seconds. You should ONLY trade when you are at least 80% confident — when indicators strongly align and the edge is clear. PASS on anything less.

## Market Mechanics
- YES token pays $1 if crypto goes UP over the 5-min window, $0 otherwise.
- NO token pays $1 if crypto goes DOWN, $0 otherwise.
- BUY = buy YES (bet UP). SELL = buy NO (bet DOWN).
- Your edge = estimated win probability minus the token price you'd pay.

## Your Trading Knowledge Base
{SKILLS_KNOWLEDGE}

## What You Receive Each Call
- Current window delta (price change since window opened) — YOUR #1 SIGNAL
- Technical indicators across 5m, 1h, 4h timeframes from Binance
- Order book depth and spread from Polymarket
- Your recent trade history with outcomes (WIN/LOSS/pending)
- Your current portfolio state

## Critical Rules
1. ONLY trade when you are at least 80% confident. Set confidence>=0.80 when indicators strongly confirm.
2. PASS on anything below 80% confidence — protect capital above all.
3. Window 35-82% elapsed is your trading zone. Outside = PASS.
4. Need overwhelming edge (5%+) to justify the trade.
5. Review your recent trades — if you're losing on a specific asset, avoid it.
6. If you just traded this exact market <30s ago, PASS (avoid doubling up).
7. Be concise: 1-2 sentences of reasoning max.
8. We invest only 5% per market — small bets, high conviction only.

## Output
Call make_trading_decision exactly once."""


# ── Tool definition ──────────────────────────────────────────────

TRADING_DECISION_TOOL = {
    "name": "make_trading_decision",
    "description": "Submit your trading decision for this market. Call exactly once.",
    "input_schema": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["BUY", "SELL", "PASS"],
                "description": "BUY = bet UP (buy YES). SELL = bet DOWN (buy NO). PASS = skip.",
            },
            "confidence": {
                "type": "number",
                "minimum": 0.0,
                "maximum": 1.0,
                "description": "Confidence level. Must be >=0.80 to trade.",
            },
            "edge_estimate": {
                "type": "number",
                "description": "Estimated edge as decimal (0.05 = 5%).",
            },
            "reasoning": {
                "type": "string",
                "description": "1-2 sentence explanation.",
            },
        },
        "required": ["action", "confidence", "edge_estimate", "reasoning"],
    },
}


# ── Enhanced indicators ─────────────────────────────────────────

def _compute_macd(prices: list[float]) -> dict[str, float]:
    """Compute MACD (12, 26, 9) from price series."""
    if len(prices) < 26:
        return {}
    ema12 = compute_ema(prices, 12)
    ema26 = compute_ema(prices, 26)
    macd_line = ema12 - ema26

    # Signal line approximation (EMA of last 9 MACD values)
    macd_values = []
    for i in range(min(9, len(prices) - 26)):
        subset = prices[:len(prices) - i]
        if len(subset) >= 26:
            macd_values.append(compute_ema(subset, 12) - compute_ema(subset, 26))
    signal_line = sum(macd_values) / len(macd_values) if macd_values else macd_line
    histogram = macd_line - signal_line

    return {
        "macd_line": round(macd_line, 4),
        "macd_signal": round(signal_line, 4),
        "macd_histogram": round(histogram, 4),
    }


def _compute_indicators(prices: list[float]) -> dict[str, Any]:
    """Compute all technical indicators for a price series."""
    if not prices or len(prices) < 5:
        return {}

    indicators: dict[str, Any] = {
        "price": prices[-1],
        "ema_5": round(compute_ema(prices, 5), 4),
        "ema_13": round(compute_ema(prices, 13), 4),
        "ema_21": round(compute_ema(prices, 21), 4) if len(prices) >= 21 else None,
        "rsi_14": round(compute_rsi(prices, 14), 1),
        "adx_14": round(compute_adx(prices, 14), 1),
        "roc_3": round(compute_roc(prices, 3) * 100, 3),
        "roc_5": round(compute_roc(prices, 5) * 100, 3) if len(prices) >= 6 else None,
        "momentum_5": round(compute_momentum(prices, 5), 4) if len(prices) >= 6 else None,
        "momentum_10": round(compute_momentum(prices, 10), 4) if len(prices) >= 11 else None,
    }

    # Bollinger Bands
    if len(prices) >= 20:
        bb_upper, bb_mid, bb_lower = compute_bollinger(prices, 20)
        bb_width = (bb_upper - bb_lower) / bb_mid if bb_mid > 0 else 0
        indicators.update({
            "bb_upper": round(bb_upper, 4),
            "bb_mid": round(bb_mid, 4),
            "bb_lower": round(bb_lower, 4),
            "bb_width_pct": round(bb_width * 100, 3),
            "bb_position": round((prices[-1] - bb_lower) / (bb_upper - bb_lower), 2) if bb_upper != bb_lower else 0.5,
            "volatility_pct": round(compute_volatility(prices, 20) * 100, 3),
        })

    # MACD
    macd = _compute_macd(prices)
    if macd:
        indicators.update(macd)

    # Price action: recent highs/lows
    recent = prices[-10:] if len(prices) >= 10 else prices
    indicators["recent_high"] = round(max(recent), 4)
    indicators["recent_low"] = round(min(recent), 4)
    indicators["price_vs_recent_range"] = round(
        (prices[-1] - min(recent)) / (max(recent) - min(recent)), 2
    ) if max(recent) != min(recent) else 0.5

    # Remove None values
    return {k: v for k, v in indicators.items() if v is not None}


def _format_recent_trades(trades: list[TradeRecord], limit: int = 10) -> str:
    """Format recent trades with outcomes for feedback loop."""
    if not trades:
        return "No trades yet — this is your first session."

    lines = []
    wins, losses, pending = 0, 0, 0
    for t in trades[:limit]:
        if t.pnl is not None and t.pnl > 0:
            status = "WIN"
            wins += 1
        elif t.pnl is not None and t.pnl < 0:
            status = "LOSS"
            losses += 1
        else:
            status = "PENDING"
            pending += 1
        pnl_str = f"${t.pnl:+.2f}" if t.pnl is not None else "pending"
        asset = ""
        for coin in ["Bitcoin", "Ethereum", "Solana", "XRP", "Dogecoin"]:
            if coin.lower() in t.market.lower():
                asset = coin[:3].upper()
                break
        lines.append(f"  {t.side.value} ${t.size:.0f} @ {t.price:.3f} → {status} {pnl_str} [{asset}] {t.market[:35]}")

    summary = f"  Summary: {wins}W / {losses}L / {pending}P"
    if wins + losses > 0:
        summary += f" ({wins/(wins+losses):.0%} win rate)"

    # Add pattern analysis
    recent_5 = trades[:5]
    recent_losses = sum(1 for t in recent_5 if t.pnl is not None and t.pnl < 0)
    recent_wins = sum(1 for t in recent_5 if t.pnl is not None and t.pnl > 0)
    if recent_losses >= 3:
        summary += "\n  ⚠ LOSING STREAK: 3+ losses in last 5. Check if you're fighting the trend."
    elif recent_wins >= 3:
        summary += "\n  ✓ HOT STREAK: 3+ wins in last 5. You're reading the market well."

    return "\n".join(lines) + "\n" + summary


def _fetch_order_book_summary(token_id: str) -> tuple[str, float]:
    """Fetch order book and return (summary_text, mid_price).

    mid_price is the real-time CLOB mid-price (avg of best bid/ask).
    Returns 0.0 if unavailable.
    """
    try:
        book = polymarket.get_order_book(token_id)
        if not book:
            return "Order book: unavailable", 0.0

        bids = book.get("bids", [])
        asks = book.get("asks", [])

        best_bid = float(bids[0]["price"]) if bids else 0
        best_ask = float(asks[0]["price"]) if asks else 0
        spread = best_ask - best_bid if best_bid and best_ask else 0
        mid_price = (best_bid + best_ask) / 2 if best_bid and best_ask else 0.0

        bid_depth = sum(float(b.get("size", 0)) for b in bids[:5])
        ask_depth = sum(float(a.get("size", 0)) for a in asks[:5])
        imbalance = (bid_depth - ask_depth) / (bid_depth + ask_depth) if (bid_depth + ask_depth) > 0 else 0

        summary = (
            f"Order book: bid={best_bid:.3f} ask={best_ask:.3f} "
            f"spread={spread:.3f} ({spread*100:.1f}%) | "
            f"bid_depth(5)=${bid_depth:.0f} ask_depth(5)=${ask_depth:.0f} "
            f"imbalance={imbalance:+.2f} ({'BUY pressure' if imbalance > 0.1 else 'SELL pressure' if imbalance < -0.1 else 'balanced'})"
        )
        return summary, mid_price
    except Exception as e:
        logger.debug(f"Order book fetch failed: {e}")
        return "Order book: unavailable", 0.0


def _build_market_context(
    market: MarketInfo,
    price_data: dict[Timeframe, list[float]],
    recent_trades: list[TradeRecord],
    portfolio_summary: str,
) -> tuple[str, float]:
    """Build the full context for Claude with all available data.

    Returns (context_text, clob_mid_price).
    """
    order_book, clob_mid_price = _fetch_order_book_summary(market.token_id_yes)

    # Determine direction hint from delta
    delta = market.window_delta
    delta_pct = delta * 100
    if abs(delta_pct) > 0.15:
        delta_hint = "VERY STRONG momentum"
    elif abs(delta_pct) > 0.08:
        delta_hint = "STRONG momentum"
    elif abs(delta_pct) > 0.04:
        delta_hint = "moderate momentum"
    else:
        delta_hint = "weak/no momentum"

    ctx = f"""MARKET: {market.question}
YES price: {market.price_yes:.3f} | NO price: {market.price_no:.3f}
Window delta: {delta:+.5f} ({delta_pct:+.3f}%) — {delta_hint} {'UP' if delta > 0 else 'DOWN' if delta < 0 else 'FLAT'}
Window elapsed: {market.window_elapsed_pct:.0%} ({"TRADEABLE" if 0.35 <= market.window_elapsed_pct <= 0.82 else "OUT OF RANGE"})
Volume: ${market.volume:,.0f} | Liquidity: ${market.liquidity:,.0f}
{order_book}
"""

    for tf_label, tf in [("5-MINUTE", Timeframe.M5), ("1-HOUR", Timeframe.H1), ("4-HOUR", Timeframe.H4)]:
        prices = price_data.get(tf, [])
        indicators = _compute_indicators(prices)
        if indicators:
            ctx += f"\n{tf_label} ({len(prices)} candles):\n"
            for k, v in indicators.items():
                ctx += f"  {k}: {v}\n"
        else:
            ctx += f"\n{tf_label}: insufficient data\n"

    ctx += f"\nYOUR RECENT TRADES:\n{_format_recent_trades(recent_trades)}\n"
    ctx += f"\nPORTFOLIO: {portfolio_summary}\n"

    return ctx, clob_mid_price


# ── Cached decision ──────────────────────────────────────────────

class _CachedDecision:
    def __init__(self, action: str, confidence: float, edge: float, reasoning: str, clob_price: float = 0.0):
        self.action = action
        self.confidence = confidence
        self.edge = edge
        self.reasoning = reasoning
        self.clob_price = clob_price  # fresh CLOB mid-price at decision time
        self.created_at = time.time()


# ── Main strategy class ─────────────────────────────────────────

class ClaudeStrategy:
    """Claude trading strategy — called every 30s per market."""

    def __init__(self) -> None:
        self._client = None
        self._decisions: dict[str, _CachedDecision] = {}
        self._initialized = False
        self._call_count = 0
        self._total_latency = 0.0
        self._errors = 0

    def _ensure_client(self) -> bool:
        if self._initialized:
            return self._client is not None

        self._initialized = True
        api_key = settings.ANTHROPIC_API_KEY
        if not api_key:
            logger.error("ANTHROPIC_API_KEY not set")
            return False

        try:
            import anthropic
            self._client = anthropic.Anthropic(api_key=api_key)
            logger.info("Claude strategy initialized (model=%s)", settings.ANTHROPIC_MODEL)
            return True
        except Exception as e:
            logger.error(f"Failed to init Anthropic client: {e}")
            return False

    def _should_analyze(self, market: MarketInfo) -> bool:
        """Relaxed filter — let Claude decide whether to trade or PASS."""
        if not is_5m_updown_market(market.question):
            return False
        # Only skip at true extremes
        if market.window_elapsed_pct < 0.20:
            return False
        if market.window_elapsed_pct > 0.90:
            return False
        # Skip if market price already at extreme (resolved)
        if market.price_yes < 0.05 or market.price_yes > 0.95:
            return False
        return True

    def _call_claude(
        self,
        market: MarketInfo,
        price_data: dict[Timeframe, list[float]],
        recent_trades: list[TradeRecord],
        portfolio_summary: str,
    ) -> _CachedDecision | None:
        if not self._client:
            return None

        context, clob_mid_price = _build_market_context(market, price_data, recent_trades, portfolio_summary)

        start = time.time()
        try:
            response = self._client.messages.create(
                model=settings.ANTHROPIC_MODEL,
                max_tokens=400,
                system=SYSTEM_PROMPT,
                tools=[TRADING_DECISION_TOOL],
                tool_choice={"type": "tool", "name": "make_trading_decision"},
                messages=[
                    {"role": "user", "content": f"Analyze and decide:\n\n{context}"}
                ],
            )

            latency = time.time() - start
            self._call_count += 1
            self._total_latency += latency

            for block in response.content:
                if block.type == "tool_use" and block.name == "make_trading_decision":
                    inp = block.input
                    decision = _CachedDecision(
                        action=inp.get("action", "PASS"),
                        confidence=float(inp.get("confidence", 0)),
                        edge=float(inp.get("edge_estimate", 0)),
                        reasoning=inp.get("reasoning", ""),
                        clob_price=clob_mid_price,
                    )
                    # Extract key indicators from price data for logging
                    m5_prices = price_data.get(Timeframe.M5, [])
                    rsi_str = f"RSI={compute_rsi(m5_prices, 14):.0f}" if len(m5_prices) >= 15 else ""
                    adx_str = f"ADX={compute_adx(m5_prices, 14):.0f}" if len(m5_prices) >= 15 else ""

                    # Coin symbol from market name
                    coin = market.question.split(" ")[0][:3].upper()

                    logger.info(
                        f"[Claude] {decision.action} {coin} | "
                        f"conf={decision.confidence:.0%} edge={decision.edge:.1%} "
                        f"Δ={market.window_delta*100:+.3f}% "
                        f"elapsed={market.window_elapsed_pct:.0%} "
                        f"clob={clob_mid_price:.3f} "
                        f"{rsi_str} {adx_str} "
                        f"({latency:.1f}s) | "
                        f"{decision.reasoning}"
                    )
                    return decision

            logger.warning(f"[Claude] No tool_use in response ({latency:.1f}s)")
            return None

        except Exception as e:
            latency = time.time() - start
            self._errors += 1
            logger.error(f"[Claude] API error ({latency:.1f}s): {e}")
            return None

    def analyze_market(
        self,
        market: MarketInfo,
        price_data: dict[Timeframe, list[float]],
        strategy_state: "StrategyState | None" = None,
    ) -> StrategyOutput:
        """Main entry point — called every bot cycle (~2s) for each market."""
        key = market.condition_id

        # Use cached decision if < 30 seconds old
        cached = self._decisions.get(key)
        if cached is not None and (time.time() - cached.created_at) < 30:
            return self._decision_to_output(cached, market, price_data)

        # Skip if market conditions are extreme
        if not self._should_analyze(market):
            return self._pass_output(market, price_data)

        # Ensure client is ready
        if not self._ensure_client():
            return self._pass_output(market, price_data)

        # Build portfolio context from agent state
        recent_trades: list[TradeRecord] = []
        portfolio_summary = "Balance: $500 | Fresh start — no trade history"

        if strategy_state is not None and hasattr(strategy_state, '_agent_state'):
            state = strategy_state._agent_state
            recent_trades = state.recent_trades[:15]
            m = state.metrics
            total = m.winning_trades + m.losing_trades
            wr = f"{m.win_rate:.0%}" if total > 0 else "N/A"
            portfolio_summary = (
                f"Balance: ${m.balance:.0f} | PnL: ${m.total_pnl:+.2f} | "
                f"Win Rate: {wr} ({m.winning_trades}W/{m.losing_trades}L) | "
                f"Positions: {m.active_positions} | Exposure: ${m.total_exposure:.0f}"
            )

        decision = self._call_claude(market, price_data, recent_trades, portfolio_summary)

        if decision is None:
            return self._pass_output(market, price_data)

        self._decisions[key] = decision
        self._cleanup_cache()
        return self._decision_to_output(decision, market, price_data)

    def _decision_to_output(
        self,
        decision: _CachedDecision,
        market: MarketInfo,
        price_data: dict[Timeframe, list[float]],
    ) -> StrategyOutput:
        signals = []
        for tf in [Timeframe.M5, Timeframe.H1, Timeframe.H4]:
            prices = price_data.get(tf, [])
            if prices and len(prices) >= 5:
                signals.append(TimeframeSignal(
                    timeframe=tf, signal=Signal.NEUTRAL,
                    confidence=decision.confidence, price=prices[-1],
                    sma_short=compute_sma(prices, 7), sma_long=compute_sma(prices, 25),
                    rsi=compute_rsi(prices, 14), momentum=0.0,
                ))
            else:
                signals.append(TimeframeSignal(
                    timeframe=tf, signal=Signal.NEUTRAL, confidence=0.0,
                    price=0.0, sma_short=0.0, sma_long=0.0, rsi=50.0, momentum=0.0,
                ))

        recommended_side = None
        token_id = market.token_id_yes
        edge = decision.edge

        # Use fresh CLOB price if available, fall back to Gamma API price
        fresh_price = decision.clob_price if decision.clob_price > 0 else market.price_yes

        if decision.action == "BUY" and decision.confidence >= 0.80 and edge > 0.05:
            recommended_side = Side.BUY
            token_id = market.token_id_yes
            composite = Signal.STRONG_BUY if decision.confidence >= 0.90 else Signal.BUY
        elif decision.action == "SELL" and decision.confidence >= 0.80 and edge > 0.05:
            recommended_side = Side.SELL
            token_id = market.token_id_no
            edge = abs(edge)
            composite = Signal.STRONG_SELL if decision.confidence >= 0.90 else Signal.SELL
        else:
            composite = Signal.NEUTRAL

        if recommended_side == Side.BUY:
            prob = min(0.99, fresh_price + edge)
        elif recommended_side == Side.SELL:
            prob = max(0.01, fresh_price - edge)
        else:
            prob = fresh_price

        return StrategyOutput(
            token_id=token_id, market=market.question, signals=signals,
            composite_signal=composite, probability_estimate=round(prob, 4),
            market_price=fresh_price, edge=round(edge, 4),
            recommended_side=recommended_side,
        )

    def _pass_output(self, market: MarketInfo, price_data: dict[Timeframe, list[float]]) -> StrategyOutput:
        signals = [
            TimeframeSignal(
                timeframe=tf, signal=Signal.NEUTRAL, confidence=0.0,
                price=0.0, sma_short=0.0, sma_long=0.0, rsi=50.0, momentum=0.0,
            )
            for tf in [Timeframe.M5, Timeframe.H1, Timeframe.H4]
        ]
        return StrategyOutput(
            token_id=market.token_id_yes, market=market.question, signals=signals,
            composite_signal=Signal.NEUTRAL, probability_estimate=market.price_yes,
            market_price=market.price_yes, edge=0.0, recommended_side=None,
        )

    def _cleanup_cache(self) -> None:
        cutoff = time.time() - 120
        stale = [k for k, v in self._decisions.items() if v.created_at < cutoff]
        for k in stale:
            del self._decisions[k]

    @property
    def stats(self) -> dict:
        avg_latency = self._total_latency / self._call_count if self._call_count > 0 else 0
        return {
            "total_calls": self._call_count,
            "avg_latency_ms": round(avg_latency * 1000),
            "cached_decisions": len(self._decisions),
            "errors": self._errors,
        }


# Module-level singleton
claude_strategy = ClaudeStrategy()


def analyze_market_claude(
    market: MarketInfo,
    price_data: dict[Timeframe, list[float]],
    strategy_state: "StrategyState | None" = None,
) -> StrategyOutput:
    """Drop-in replacement matching TradingAgent.strategy_fn signature."""
    return claude_strategy.analyze_market(market, price_data, strategy_state)
