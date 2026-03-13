"""Enhanced deterministic trading strategy for crypto binary markets.

Replaces Claude API calls with a codified multi-strategy scoring engine.
Implements all 10 strategies from skills.md as weighted votes.
Same function signature as analyze_market_claude — drop-in replacement.
"""

from __future__ import annotations

import logging
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
    is_15m_updown_market,
    is_1h_updown_market,
    is_4h_updown_market,
    is_daily_updown_market,
)
from backend.bot.claude_strategy import _compute_macd, _fetch_order_book_summary
from backend.models import (
    MarketInfo,
    Side,
    Signal,
    StrategyOutput,
    Timeframe,
    TimeframeSignal,
)

if TYPE_CHECKING:
    from backend.bot.agent import StrategyState

logger = logging.getLogger(__name__)


# ── Indicator helpers ────────────────────────────────────────────

def _compute_indicators(prices: list[float]) -> dict[str, Any]:
    """Compute all technical indicators for a price series."""
    if not prices or len(prices) < 5:
        return {}

    indicators: dict[str, Any] = {
        "price": prices[-1],
        "ema_5": compute_ema(prices, 5),
        "ema_13": compute_ema(prices, 13),
        "ema_21": compute_ema(prices, 21) if len(prices) >= 21 else None,
        "rsi_14": compute_rsi(prices, 14),
        "adx_14": compute_adx(prices, 14),
        "roc_3": compute_roc(prices, 3) * 100,  # as percentage
        "roc_5": compute_roc(prices, 5) * 100 if len(prices) >= 6 else None,
        "momentum_5": compute_momentum(prices, 5) if len(prices) >= 6 else None,
    }

    # Bollinger Bands
    if len(prices) >= 20:
        bb_upper, bb_mid, bb_lower = compute_bollinger(prices, 20)
        bb_width = (bb_upper - bb_lower) / bb_mid if bb_mid > 0 else 0
        indicators.update({
            "bb_upper": bb_upper,
            "bb_mid": bb_mid,
            "bb_lower": bb_lower,
            "bb_width_pct": bb_width * 100,
            "bb_position": (prices[-1] - bb_lower) / (bb_upper - bb_lower) if bb_upper != bb_lower else 0.5,
            "volatility_pct": compute_volatility(prices, 20) * 100,
        })

    # MACD
    macd = _compute_macd(prices)
    if macd:
        indicators.update(macd)

    # Price action: recent range
    recent = prices[-10:] if len(prices) >= 10 else prices
    indicators["price_vs_recent_range"] = (
        (prices[-1] - min(recent)) / (max(recent) - min(recent))
        if max(recent) != min(recent) else 0.5
    )

    return {k: v for k, v in indicators.items() if v is not None}


def _get_order_book_imbalance(token_id: str) -> float:
    """Fetch order book and return imbalance (-1 to +1)."""
    try:
        book = polymarket.get_order_book(token_id)
        if not book:
            return 0.0
        bids = book.get("bids", [])
        asks = book.get("asks", [])
        bid_depth = sum(float(b.get("size", 0)) for b in bids[:5])
        ask_depth = sum(float(a.get("size", 0)) for a in asks[:5])
        total = bid_depth + ask_depth
        if total == 0:
            return 0.0
        return (bid_depth - ask_depth) / total
    except Exception:
        return 0.0


# ── Strategy scoring engine ──────────────────────────────────────

def _score_strategies(
    m5_ind: dict[str, Any],
    h1_ind: dict[str, Any],
    h4_ind: dict[str, Any],
    order_book_imbalance: float,
) -> tuple[float, str]:
    """Score all 10 strategies. Returns (weighted_score, reasoning)."""

    votes: list[tuple[str, float, float]] = []  # (name, vote, weight)

    # ── Strategy 1: MACD + EMA Crossover (weight 0.15) ──
    ema5 = m5_ind.get("ema_5", 0)
    ema13 = m5_ind.get("ema_13", 0)
    macd_hist = m5_ind.get("macd_histogram", 0)

    if ema5 > ema13 and macd_hist > 0:
        votes.append(("MACD+EMA", +1.0, 0.15))
    elif ema5 < ema13 and macd_hist < 0:
        votes.append(("MACD+EMA", -1.0, 0.15))
    else:
        votes.append(("MACD+EMA", 0.0, 0.15))

    # ── Strategy 2: RSI Momentum (weight 0.10) ──
    rsi = m5_ind.get("rsi_14", 50)
    roc3 = m5_ind.get("roc_3", 0)  # use ROC as proxy for "rising/falling"

    if 55 <= rsi <= 75 and roc3 > 0:
        votes.append(("RSI", +1.0, 0.10))
    elif 25 <= rsi <= 45 and roc3 < 0:
        votes.append(("RSI", -1.0, 0.10))
    else:
        votes.append(("RSI", 0.0, 0.10))

    # ── Strategy 3: BB Squeeze Breakout (weight 0.10) ──
    bb_width = m5_ind.get("bb_width_pct", 1.0)
    bb_pos = m5_ind.get("bb_position", 0.5)

    if bb_width < 0.5:
        # Squeeze detected — check for breakout
        if bb_pos > 0.80 and roc3 > 0:
            votes.append(("BB_Squeeze", +1.0, 0.10))
        elif bb_pos < 0.20 and roc3 < 0:
            votes.append(("BB_Squeeze", -1.0, 0.10))
        else:
            votes.append(("BB_Squeeze", 0.0, 0.10))
    else:
        votes.append(("BB_Squeeze", 0.0, 0.10))

    # ── Strategy 4: ADX Trend Filter (weight 0.15) ──
    adx = m5_ind.get("adx_14", 0)
    adx_modifier = 1.0

    if adx > 30:
        # Strong trend — boost directional signals
        direction = +1.0 if ema5 > ema13 else -1.0
        votes.append(("ADX", direction * 0.8, 0.15))
        adx_modifier = 1.3
    elif adx > 20:
        direction = +1.0 if ema5 > ema13 else -1.0
        votes.append(("ADX", direction * 0.4, 0.15))
    elif adx < 12:
        # Dead market — strong dampener
        votes.append(("ADX", 0.0, 0.15))
        adx_modifier = 0.5
    else:
        votes.append(("ADX", 0.0, 0.15))
        adx_modifier = 0.7

    # ── Strategy 5: EMA Ribbon (weight 0.10) ──
    ema21 = m5_ind.get("ema_21")
    if ema21 is not None:
        if ema5 > ema13 > ema21:
            votes.append(("EMA_Ribbon", +1.0, 0.10))
        elif ema5 < ema13 < ema21:
            votes.append(("EMA_Ribbon", -1.0, 0.10))
        else:
            votes.append(("EMA_Ribbon", 0.0, 0.10))
    else:
        votes.append(("EMA_Ribbon", 0.0, 0.10))

    # ── Strategy 6: ROC Momentum Burst (weight 0.05) ──
    roc5 = m5_ind.get("roc_5", 0)

    if roc3 > 0.1 and roc5 > 0.15:
        votes.append(("ROC_Burst", +1.0, 0.05))
    elif roc3 < -0.1 and roc5 < -0.15:
        votes.append(("ROC_Burst", -1.0, 0.05))
    else:
        votes.append(("ROC_Burst", 0.0, 0.05))

    # ── Strategy 7: Multi-TF Confluence (weight 0.15) ──
    m5_bullish = (m5_ind.get("ema_5", 0) > m5_ind.get("ema_13", 0)
                  and m5_ind.get("rsi_14", 50) > 55)
    m5_bearish = (m5_ind.get("ema_5", 0) < m5_ind.get("ema_13", 0)
                  and m5_ind.get("rsi_14", 50) < 45)

    h1_bullish = (h1_ind.get("ema_5", 0) > h1_ind.get("ema_13", 0)
                  and h1_ind.get("rsi_14", 50) > 55)
    h1_bearish = (h1_ind.get("ema_5", 0) < h1_ind.get("ema_13", 0)
                  and h1_ind.get("rsi_14", 50) < 45)

    h4_bullish = h4_ind.get("rsi_14", 50) > 50 and (
        h4_ind.get("price", 0) > h4_ind.get("ema_21", h4_ind.get("price", 0))
    )
    h4_bearish = h4_ind.get("rsi_14", 50) < 50 and (
        h4_ind.get("price", 0) < h4_ind.get("ema_21", h4_ind.get("price", 0))
    )

    bullish_count = sum([m5_bullish, h1_bullish, h4_bullish])
    bearish_count = sum([m5_bearish, h1_bearish, h4_bearish])

    if bullish_count == 3:
        votes.append(("Multi_TF", +1.0, 0.15))
    elif bearish_count == 3:
        votes.append(("Multi_TF", -1.0, 0.15))
    elif bullish_count == 2:
        votes.append(("Multi_TF", +0.5, 0.15))
    elif bearish_count == 2:
        votes.append(("Multi_TF", -0.5, 0.15))
    else:
        votes.append(("Multi_TF", 0.0, 0.15))

    # ── Strategy 8: Order Book Imbalance (weight 0.05) ──
    if order_book_imbalance > 0.20:
        votes.append(("OrderBook", +1.0, 0.05))
    elif order_book_imbalance < -0.20:
        votes.append(("OrderBook", -1.0, 0.05))
    else:
        votes.append(("OrderBook", 0.0, 0.05))

    # ── Strategy 10: Price Action Range (weight 0.05) ──
    pvr = m5_ind.get("price_vs_recent_range", 0.5)

    if pvr > 0.80:
        votes.append(("PriceRange", +1.0, 0.05))
    elif pvr < 0.20:
        votes.append(("PriceRange", -1.0, 0.05))
    else:
        votes.append(("PriceRange", 0.0, 0.05))

    # ── Weighted sum ──
    raw_score = sum(vote * weight for _, vote, weight in votes)

    # ── Strategy 9: Volatility Regime (modifier) ──
    vol = m5_ind.get("volatility_pct", 0)
    if vol > 2.0:
        raw_score *= 0.6  # high vol dampens
    elif vol > 1.0:
        raw_score *= 0.8
    elif bb_width < 0.5 and abs(raw_score) > 0.1:
        raw_score *= 1.2  # low vol squeeze boosts breakout signals

    # Apply ADX modifier
    raw_score *= adx_modifier

    # Build reasoning from top contributing strategies
    active = [(name, vote * weight) for name, vote, weight in votes if abs(vote) > 0.1]
    active.sort(key=lambda x: abs(x[1]), reverse=True)
    top_reasons = [f"{name}={'BUY' if contrib > 0 else 'SELL'}" for name, contrib in active[:3]]
    reasoning = ", ".join(top_reasons) if top_reasons else "no clear signal"

    return float(np.clip(raw_score, -1.0, 1.0)), reasoning


# ── Main entry point ─────────────────────────────────────────────

def analyze_market_enhanced(
    market: MarketInfo,
    price_data: dict[Timeframe, list[float]],
    strategy_state: "StrategyState | None" = None,
) -> StrategyOutput:
    """Deterministic strategy — drop-in replacement for analyze_market_claude."""

    # ── Pre-filters ──
    is_5m = is_5m_updown_market(market.question)
    is_15m = is_15m_updown_market(market.question)
    is_1h = is_1h_updown_market(market.question)
    is_4h = is_4h_updown_market(market.question)
    is_daily = is_daily_updown_market(market.question)

    if not (is_5m or is_15m or is_1h or is_4h or is_daily):
        return _pass_output(market, price_data)

    # Extreme price filter
    if market.price_yes < 0.05 or market.price_yes > 0.95:
        return _pass_output(market, price_data)

    # Window timing gate
    elapsed = market.window_elapsed_pct
    if is_5m and (elapsed < 0.35 or elapsed > 0.82):
        return _pass_output(market, price_data)
    if is_15m and (elapsed < 0.30 or elapsed > 0.85):
        return _pass_output(market, price_data)
    if is_1h and (elapsed < 0.15 or elapsed > 0.90):
        return _pass_output(market, price_data)
    if is_4h and (elapsed < 0.10 or elapsed > 0.90):
        return _pass_output(market, price_data)

    # ── Compute indicators across timeframes ──
    m5_prices = price_data.get(Timeframe.M5, [])
    h1_prices = price_data.get(Timeframe.H1, [])
    h4_prices = price_data.get(Timeframe.H4, [])

    m5_ind = _compute_indicators(m5_prices)
    h1_ind = _compute_indicators(h1_prices)
    h4_ind = _compute_indicators(h4_prices)

    if not m5_ind:
        return _pass_output(market, price_data)

    # ── Order book imbalance ──
    ob_imbalance = _get_order_book_imbalance(market.token_id_yes)

    # ── CLOB mid-price ──
    _, clob_mid_price = _fetch_order_book_summary(market.token_id_yes)
    fresh_price = clob_mid_price if clob_mid_price > 0 else market.price_yes

    # ── Score strategies ──
    strategy_score, reasoning = _score_strategies(m5_ind, h1_ind, h4_ind, ob_imbalance)

    # ── Window delta blend — weight depends on market duration ──
    w_delta = market.window_delta
    if is_5m and abs(w_delta) > 0.0005:
        delta_score = float(np.clip(w_delta * 500, -0.9, 0.9))
        elapsed_boost = 1.0 + elapsed * 1.5
        delta_score *= elapsed_boost
        # 60% delta, 40% strategy
        final_score = 0.60 * delta_score + 0.40 * strategy_score
        reasoning = f"delta={w_delta*100:+.3f}%, " + reasoning
    elif is_15m and abs(w_delta) > 0.0005:
        delta_score = float(np.clip(w_delta * 300, -0.9, 0.9))
        final_score = 0.50 * delta_score + 0.50 * strategy_score
        reasoning = f"delta={w_delta*100:+.3f}%, " + reasoning
    elif is_1h and abs(w_delta) > 0.0008:
        delta_score = float(np.clip(w_delta * 200, -0.7, 0.7))
        final_score = 0.45 * delta_score + 0.55 * strategy_score
        reasoning = f"delta={w_delta*100:+.3f}%, " + reasoning
    elif is_4h and abs(w_delta) > 0.001:
        delta_score = float(np.clip(w_delta * 150, -0.6, 0.6))
        final_score = 0.40 * delta_score + 0.60 * strategy_score
        reasoning = f"delta={w_delta*100:+.3f}%, " + reasoning
    elif is_daily and abs(w_delta) > 0.001:
        delta_score = float(np.clip(w_delta * 100, -0.4, 0.4))
        final_score = 0.40 * delta_score + 0.60 * strategy_score
    else:
        final_score = strategy_score

    final_score = float(np.clip(final_score, -1.0, 1.0))

    # ── Streak penalty ──
    if strategy_state is not None:
        recent = list(strategy_state.recent_outcomes)[-5:]
        losses = sum(1 for o in recent if not o)
        if losses >= 4:
            final_score *= 0.3
        elif losses >= 3:
            final_score *= 0.5

    # ── Confidence mapping ──
    abs_score = abs(final_score)
    if abs_score > 0.65:
        confidence = 0.90 + min((abs_score - 0.65) * 0.3, 0.09)
    elif abs_score > 0.45:
        confidence = 0.80 + (abs_score - 0.45) * 0.5
    else:
        confidence = abs_score * 1.5  # below 80% → PASS

    confidence = min(confidence, 0.99)

    # ── Edge calculation ──
    if is_5m or is_15m:
        max_adj = 0.35
    elif is_1h:
        max_adj = 0.30
    elif is_4h:
        max_adj = 0.28
    else:
        max_adj = 0.25
    adjustment = final_score * max_adj
    our_prob = max(0.01, min(0.99, fresh_price + adjustment))
    edge = abs(our_prob - fresh_price)

    # ── Decision ──
    action = "PASS"
    recommended_side = None
    token_id = market.token_id_yes

    if confidence >= 0.80 and edge > 0.05:
        if final_score > 0:
            action = "BUY"
            recommended_side = Side.BUY
            token_id = market.token_id_yes
        else:
            action = "SELL"
            recommended_side = Side.SELL
            token_id = market.token_id_no

    # ── Composite signal ──
    if action == "BUY":
        composite = Signal.STRONG_BUY if confidence >= 0.90 else Signal.BUY
    elif action == "SELL":
        composite = Signal.STRONG_SELL if confidence >= 0.90 else Signal.SELL
    else:
        composite = Signal.NEUTRAL

    # Probability for output
    if recommended_side == Side.BUY:
        prob = min(0.99, fresh_price + edge)
    elif recommended_side == Side.SELL:
        prob = max(0.01, fresh_price - edge)
    else:
        prob = fresh_price

    # ── Build timeframe signals ──
    signals = []
    for tf in [Timeframe.M5, Timeframe.H1, Timeframe.H4]:
        prices = price_data.get(tf, [])
        if prices and len(prices) >= 5:
            signals.append(TimeframeSignal(
                timeframe=tf, signal=Signal.NEUTRAL,
                confidence=confidence, price=prices[-1],
                sma_short=compute_sma(prices, 7),
                sma_long=compute_sma(prices, 25),
                rsi=compute_rsi(prices, 14), momentum=0.0,
            ))
        else:
            signals.append(TimeframeSignal(
                timeframe=tf, signal=Signal.NEUTRAL, confidence=0.0,
                price=0.0, sma_short=0.0, sma_long=0.0, rsi=50.0, momentum=0.0,
            ))

    # Log decision
    coin = market.question.split(" ")[0][:3].upper()
    rsi_str = f"RSI={m5_ind.get('rsi_14', 0):.0f}" if m5_ind else ""
    adx_str = f"ADX={m5_ind.get('adx_14', 0):.0f}" if m5_ind else ""

    logger.info(
        f"[Enhanced] {action} {coin} | "
        f"conf={confidence:.0%} edge={edge:.1%} "
        f"score={final_score:+.3f} "
        f"Δ={market.window_delta*100:+.3f}% "
        f"elapsed={elapsed:.0%} "
        f"{rsi_str} {adx_str} | "
        f"{reasoning}"
    )

    output = StrategyOutput(
        token_id=token_id, market=market.question, signals=signals,
        composite_signal=composite, probability_estimate=round(prob, 4),
        market_price=fresh_price, edge=round(edge, 4),
        recommended_side=recommended_side,
    )

    return output


def _pass_output(market: MarketInfo, price_data: dict[Timeframe, list[float]]) -> StrategyOutput:
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
