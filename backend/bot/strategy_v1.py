"""Multi-timeframe signal strategy v1 — simpler, more aggressive.

Original strategy without v2's additions:
  - No ADX trend filter
  - No Bollinger Band analysis
  - No volatility regime filter
  - No cooldown guard between trades
  - No streak-based sizing penalties
  - Fixed thresholds and adjustments

Achieved 66% WR, +$269 on $200 in backtesting.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import numpy as np

from backend.bot.strategy import (
    # Reuse all shared indicator functions — no duplication
    SIGNAL_VALUES,
    TF_WEIGHTS_5M,
    TF_WEIGHTS_STANDARD,
    compute_ema,
    compute_momentum,
    compute_roc,
    compute_rsi,
    compute_sma,
    is_5m_updown_market,
)
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

# v1 thresholds — balanced selectivity
MIN_EDGE_V1 = 0.02  # 2% edge minimum (up from 1.5%, down from 3%)
MAX_ADJ_V1 = 0.24  # Moderate probability adjustment


def price_signal_v1(prices: list[float], timeframe: Timeframe) -> TimeframeSignal:
    """Generate a signal using v1's simpler scoring — no ADX, no BB, no vol filter."""
    if not prices or len(prices) < 5:
        return TimeframeSignal(
            timeframe=timeframe, signal=Signal.NEUTRAL, confidence=0.0,
            price=0.0, sma_short=0.0, sma_long=0.0, rsi=50.0, momentum=0.0,
        )

    current = prices[-1]
    ema_fast = compute_ema(prices, 5)
    ema_slow = compute_ema(prices, 13)
    sma_short = compute_sma(prices, 7)
    sma_long = compute_sma(prices, 25)
    rsi = compute_rsi(prices, 14)
    roc_3 = compute_roc(prices, 3)

    score = 0.0

    # ── 1. EMA Crossover (directional bias) ──
    if ema_slow > 0:
        ema_sep = (ema_fast - ema_slow) / ema_slow
        score += np.clip(ema_sep * 30, -0.4, 0.4)

    # ── 2. RSI — overbought/oversold ──
    if rsi > 70:
        score -= 0.15 + (rsi - 70) / 150
    elif rsi < 30:
        score += 0.15 + (30 - rsi) / 150
    elif rsi > 55:
        score += 0.08
    elif rsi < 45:
        score -= 0.08

    # ── 3. Short-term rate of change (dampened to reduce noise) ──
    score += np.clip(roc_3 * 25, -0.30, 0.30)

    # ── 4. Trend confirmation via SMA alignment ──
    if current > sma_long and sma_short > sma_long:
        score += 0.1
    elif current < sma_long and sma_short < sma_long:
        score -= 0.1

    # No ADX dampening (v2 feature)
    # No Bollinger Band scoring (v2 feature)
    # No volatility dampener (v2 feature)

    # ── Map to signal — v1 thresholds (higher bar) ──
    if score > 0.35:
        signal = Signal.STRONG_BUY
    elif score > 0.10:
        signal = Signal.BUY
    elif score < -0.35:
        signal = Signal.STRONG_SELL
    elif score < -0.10:
        signal = Signal.SELL
    else:
        signal = Signal.NEUTRAL

    confidence = min(abs(score), 1.0)

    return TimeframeSignal(
        timeframe=timeframe,
        signal=signal,
        confidence=confidence,
        price=current,
        sma_short=sma_short,
        sma_long=sma_long,
        rsi=rsi,
        momentum=compute_momentum(prices),
    )


def analyze_market_v1(
    market: MarketInfo,
    price_data: dict[Timeframe, list[float]],
    strategy_state: "StrategyState | None" = None,
) -> StrategyOutput:
    """V1 multi-timeframe analysis — simpler, no filters, no cooldown.

    Same interface as v2's analyze_market() for interchangeability.
    strategy_state is accepted for interface compatibility but v1 doesn't use it.
    """
    signals: list[TimeframeSignal] = []

    for tf in [Timeframe.M5, Timeframe.H1, Timeframe.H4]:
        prices = price_data.get(tf, [])
        if not prices:
            signals.append(TimeframeSignal(
                timeframe=tf, signal=Signal.NEUTRAL, confidence=0.0,
                price=0.0, sma_short=0.0, sma_long=0.0, rsi=50.0, momentum=0.0,
            ))
            continue
        signals.append(price_signal_v1(prices, tf))

    # Select weights (same as v2)
    is_5m = is_5m_updown_market(market.question)
    weights = TF_WEIGHTS_5M if is_5m else TF_WEIGHTS_STANDARD

    # Composite weighted signal
    weighted_score = sum(
        SIGNAL_VALUES[s.signal] * s.confidence * weights[s.timeframe]
        for s in signals
    )

    # ── Composite classification — v1 thresholds (tighter) ──
    if weighted_score > 0.30:
        composite = Signal.STRONG_BUY
    elif weighted_score > 0.07:
        composite = Signal.BUY
    elif weighted_score < -0.30:
        composite = Signal.STRONG_SELL
    elif weighted_score < -0.07:
        composite = Signal.SELL
    else:
        composite = Signal.NEUTRAL

    # ── Window delta signal (dominant for 5-min markets) ──
    w_delta = market.window_delta
    w_elapsed = market.window_elapsed_pct

    if is_5m and abs(w_delta) > 0.0005 and w_elapsed > 0.30:
        # Window delta is the #1 predictor for 5-min binary outcomes
        elapsed_boost = 1.0 + w_elapsed * 1.0
        delta_score = np.clip(w_delta * 400, -0.8, 0.8) * elapsed_boost
        # v1 blends more aggressively toward delta (80/20 vs v2's 70/30)
        weighted_score = 0.80 * delta_score + 0.20 * weighted_score

    # Cap blended score to prevent unrealistic edges
    weighted_score = float(np.clip(weighted_score, -0.60, 0.60))

    # ── Probability estimate — fixed max_adj for all markets ──
    base_prob = market.price_yes
    adjustment = weighted_score * MAX_ADJ_V1
    our_prob = max(0.01, min(0.99, base_prob + adjustment))

    edge = our_prob - market.price_yes

    # ── Trade decision — with momentum agreement + RSI guard ──
    recommended_side = None
    token_id = market.token_id_yes

    # ── Timing gate for 5-min markets ──
    # v1 trades slightly earlier than v2 (35% vs 40%) — more aggressive
    if is_5m and w_elapsed < 0.35:
        return StrategyOutput(
            token_id=token_id, market=market.question, signals=signals,
            composite_signal=composite, probability_estimate=round(our_prob, 4),
            market_price=market.price_yes, edge=round(edge, 4),
            recommended_side=None,
        )

    # Get M5 indicators for agreement checks
    m5_prices = price_data.get(Timeframe.M5, [])
    m5_rsi = compute_rsi(m5_prices, 14) if len(m5_prices) > 15 else 50.0
    m5_roc = compute_roc(m5_prices, 3) if len(m5_prices) > 4 else 0.0
    m5_ema_fast = compute_ema(m5_prices, 5) if len(m5_prices) > 5 else 0.0
    m5_ema_slow = compute_ema(m5_prices, 13) if len(m5_prices) > 13 else 0.0
    ema_bullish = m5_ema_fast > m5_ema_slow
    roc_bullish = m5_roc > 0

    if edge > MIN_EDGE_V1:
        # Strong window delta overrides RSI guard (confirmed direction)
        if is_5m and abs(w_delta) > 0.001 and w_elapsed > 0.50:
            recommended_side = Side.BUY
            token_id = market.token_id_yes
        elif m5_rsi > 70:
            pass  # skip — overbought, likely to reverse
        elif ema_bullish or roc_bullish:
            recommended_side = Side.BUY
            token_id = market.token_id_yes
    elif edge < -MIN_EDGE_V1:
        if is_5m and abs(w_delta) > 0.001 and w_elapsed > 0.50:
            recommended_side = Side.SELL
            token_id = market.token_id_no
            edge = -edge
        elif m5_rsi < 30:
            pass  # skip — oversold, likely to bounce
        elif not ema_bullish or not roc_bullish:
            recommended_side = Side.SELL
            token_id = market.token_id_no
            edge = -edge  # normalize

    return StrategyOutput(
        token_id=token_id,
        market=market.question,
        signals=signals,
        composite_signal=composite,
        probability_estimate=round(our_prob, 4),
        market_price=market.price_yes,
        edge=round(edge, 4),
        recommended_side=recommended_side,
    )
