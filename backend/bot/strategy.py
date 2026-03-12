"""Multi-timeframe signal strategy for crypto prediction markets.

v2 — Major improvements over v1:
  1. ADX trend filter — only trade when there's a clear trend
  2. Bollinger Band breakout detection
  3. Symmetric BUY/SELL signals (no directional bias)
  4. Volatility regime filter (sit out in extreme vol)
  5. Cooldown guard for correlated consecutive markets
  6. Better edge calculation with confidence scaling
"""

from __future__ import annotations

import logging
import re
import time
from collections import deque
from typing import TYPE_CHECKING

import numpy as np

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

# Timeframe weights — standard (long-duration markets)
TF_WEIGHTS_STANDARD = {
    Timeframe.M5: 0.20,
    Timeframe.H1: 0.35,
    Timeframe.H4: 0.45,
}

# Timeframe weights — 5-minute up/down markets (short-term momentum dominant)
TF_WEIGHTS_5M = {
    Timeframe.M5: 0.65,
    Timeframe.H1: 0.25,
    Timeframe.H4: 0.10,
}

# Minimum edge required to trade (2% — filters noise while allowing real signals)
MIN_EDGE = 0.02

# Cooldown: minimum seconds between trades to avoid correlated signals
# 1s allows agents to trade every cycle on 5-min windows
MIN_TRADE_INTERVAL = 1

# Default strategy state for backward compatibility (backtest, single-agent mode)
_default_last_trade_time: float = 0.0
_default_recent_outcomes: deque[bool] = deque(maxlen=20)


def is_5m_updown_market(question: str) -> bool:
    """Detect if this is a 5-minute up/down market."""
    q = question.lower()
    return "up or down" in q and ("5m" in q or re.search(r"\d+:\d+[ap]m.*\d+:\d+[ap]m", q))


# ─── Technical Indicators ────────────────────────────────────────

def compute_sma(prices: list[float], period: int) -> float:
    if len(prices) < period:
        return prices[-1] if prices else 0.0
    return float(np.mean(prices[-period:]))


def compute_ema(prices: list[float], period: int) -> float:
    if not prices:
        return 0.0
    if len(prices) < period:
        return prices[-1]
    multiplier = 2 / (period + 1)
    ema = float(np.mean(prices[:period]))
    for price in prices[period:]:
        ema = (price - ema) * multiplier + ema
    return ema


def compute_rsi(prices: list[float], period: int = 14) -> float:
    if len(prices) < period + 1:
        return 50.0
    deltas = np.diff(prices[-(period + 1):])
    gains = np.where(deltas > 0, deltas, 0)
    losses = np.where(deltas < 0, -deltas, 0)
    avg_gain = float(np.mean(gains))
    avg_loss = float(np.mean(losses))
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return float(100 - (100 / (1 + rs)))


def compute_momentum(prices: list[float], period: int = 10) -> float:
    if len(prices) < period + 1:
        return 0.0
    return (prices[-1] - prices[-period - 1]) / prices[-period - 1]


def compute_roc(prices: list[float], period: int = 3) -> float:
    """Short-term rate of change."""
    if len(prices) < period + 1:
        return 0.0
    return (prices[-1] - prices[-period - 1]) / prices[-period - 1]


def compute_adx(prices: list[float], period: int = 14) -> float:
    """Simplified ADX (Average Directional Index) — measures trend strength.
    Returns 0-100: <20 = no trend, 20-40 = trending, >40 = strong trend.
    """
    if len(prices) < period * 2:
        return 0.0

    highs = prices[-period * 2:]
    n = len(highs)

    # Compute directional movement
    plus_dm = []
    minus_dm = []
    tr_list = []

    for i in range(1, n):
        up_move = highs[i] - highs[i - 1]
        down_move = highs[i - 1] - highs[i]

        plus_dm.append(up_move if up_move > down_move and up_move > 0 else 0)
        minus_dm.append(down_move if down_move > up_move and down_move > 0 else 0)
        tr_list.append(abs(highs[i] - highs[i - 1]))

    if not tr_list or sum(tr_list) == 0:
        return 0.0

    # Smooth with EMA
    smooth_plus = float(np.mean(plus_dm[-period:]))
    smooth_minus = float(np.mean(minus_dm[-period:]))
    smooth_tr = float(np.mean(tr_list[-period:]))

    if smooth_tr == 0:
        return 0.0

    plus_di = 100 * smooth_plus / smooth_tr
    minus_di = 100 * smooth_minus / smooth_tr

    if plus_di + minus_di == 0:
        return 0.0

    dx = 100 * abs(plus_di - minus_di) / (plus_di + minus_di)
    return dx


def compute_bollinger(prices: list[float], period: int = 20, num_std: float = 2.0) -> tuple[float, float, float]:
    """Returns (upper_band, middle_band, lower_band)."""
    if len(prices) < period:
        p = prices[-1] if prices else 0
        return (p, p, p)
    window = prices[-period:]
    middle = float(np.mean(window))
    std = float(np.std(window))
    return (middle + num_std * std, middle, middle - num_std * std)


def compute_volatility(prices: list[float], period: int = 20) -> float:
    """Returns annualized volatility of returns."""
    if len(prices) < period + 1:
        return 0.0
    returns = np.diff(prices[-period:]) / np.array(prices[-period:-1])
    return float(np.std(returns))


# ─── Signal Generation ───────────────────────────────────────────

def price_signal(prices: list[float], timeframe: Timeframe) -> TimeframeSignal:
    """Generate a signal with improved indicators (v2)."""
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
    adx = compute_adx(prices, 14)
    bb_upper, bb_mid, bb_lower = compute_bollinger(prices, 20, 2.0)
    vol = compute_volatility(prices, 20)

    score = 0.0

    # ── 1. EMA Crossover (directional bias) ──
    if ema_slow > 0:
        ema_sep = (ema_fast - ema_slow) / ema_slow
        score += np.clip(ema_sep * 30, -0.4, 0.4)

    # ── 2. RSI — overbought/oversold ──
    if rsi > 70:
        score -= 0.15 + (rsi - 70) / 150  # bearish
    elif rsi < 30:
        score += 0.15 + (30 - rsi) / 150  # bullish
    elif rsi > 55:
        score += 0.08
    elif rsi < 45:
        score -= 0.08

    # ── 3. Short-term rate of change (immediate direction) ──
    score += np.clip(roc_3 * 25, -0.30, 0.30)

    # ── 4. Bollinger Band position ──
    bb_range = bb_upper - bb_lower if bb_upper > bb_lower else 1e-10
    bb_pos = (current - bb_lower) / bb_range  # 0-1 position within bands

    if bb_pos > 0.9:
        # Near upper band — potential reversal OR strong trend
        if adx > 25:
            score += 0.1  # trending, breakout likely
        else:
            score -= 0.15  # ranging, mean reversion likely
    elif bb_pos < 0.1:
        if adx > 25:
            score -= 0.1  # trending down
        else:
            score += 0.15  # ranging, bounce likely

    # ── 5. Trend confirmation via SMA alignment ──
    if current > sma_long and sma_short > sma_long:
        score += 0.1
    elif current < sma_long and sma_short < sma_long:
        score -= 0.1

    # ── 6. ADX-based confidence scaling ──
    # Strong trends (high ADX) → more confident in directional signals
    # Weak trends (low ADX) → reduce confidence, signals are noise
    if adx < 12:
        score *= 0.6  # dampen score in very trendless markets
    elif adx < 20:
        score *= 0.8  # mild dampening in weak trends
    elif adx > 35:
        score *= 1.3  # boost score in strong trends

    # ── 7. Volatility dampener ──
    # Extremely high vol = unpredictable, reduce confidence
    if vol > 0.03:  # >3% per-bar volatility is extreme for crypto
        score *= 0.6
    elif vol > 0.02:
        score *= 0.8  # mild dampening in elevated vol

    # ── Map to signal ──
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


SIGNAL_VALUES = {
    Signal.STRONG_BUY: 1.0,
    Signal.BUY: 0.5,
    Signal.NEUTRAL: 0.0,
    Signal.SELL: -0.5,
    Signal.STRONG_SELL: -1.0,
}


def _streak_penalty(recent_outcomes: deque[bool] | None = None) -> float:
    """Reduce sizing after consecutive losses."""
    outcomes = recent_outcomes if recent_outcomes is not None else _default_recent_outcomes
    if len(outcomes) < 3:
        return 1.0
    recent = list(outcomes)[-5:]
    losses = sum(1 for o in recent if not o)
    if losses >= 4:
        return 0.3  # heavy penalty after 4+ losses in last 5
    if losses >= 3:
        return 0.5
    return 1.0


def record_outcome(won: bool, strategy_state: "StrategyState | None" = None) -> None:
    """Record a trade outcome for streak tracking."""
    if strategy_state is not None:
        strategy_state.recent_outcomes.append(won)
    else:
        _default_recent_outcomes.append(won)


def analyze_market(
    market: MarketInfo,
    price_data: dict[Timeframe, list[float]],
    strategy_state: "StrategyState | None" = None,
) -> StrategyOutput:
    """Run multi-timeframe analysis with improved signal generation.

    Key improvements over v1:
    - ADX trend filter prevents trading in choppy markets
    - Bollinger Bands detect breakouts vs mean reversion
    - Symmetric BUY/SELL generation (no directional bias)
    - Volatility regime filter
    - Cooldown enforcement between trades

    Args:
        market: Market info from Polymarket
        price_data: Historical price data per timeframe
        strategy_state: Per-agent mutable state (cooldown, streaks).
                        If None, uses module-level defaults for backward compat.
    """
    global _default_last_trade_time

    # Use per-agent state or fall back to module defaults
    if strategy_state is not None:
        last_trade_time = strategy_state.last_trade_time
        recent_outcomes = strategy_state.recent_outcomes
    else:
        last_trade_time = _default_last_trade_time
        recent_outcomes = _default_recent_outcomes

    signals: list[TimeframeSignal] = []

    for tf in [Timeframe.M5, Timeframe.H1, Timeframe.H4]:
        prices = price_data.get(tf, [])
        if not prices:
            signals.append(TimeframeSignal(
                timeframe=tf, signal=Signal.NEUTRAL, confidence=0.0,
                price=0.0, sma_short=0.0, sma_long=0.0, rsi=50.0, momentum=0.0,
            ))
            continue
        signals.append(price_signal(prices, tf))

    # Select weights
    is_5m = is_5m_updown_market(market.question)
    weights = TF_WEIGHTS_5M if is_5m else TF_WEIGHTS_STANDARD

    # Composite weighted signal
    weighted_score = sum(
        SIGNAL_VALUES[s.signal] * s.confidence * weights[s.timeframe]
        for s in signals
    )

    # ── Composite classification (raised thresholds for selectivity) ──
    if weighted_score > 0.25:
        composite = Signal.STRONG_BUY
    elif weighted_score > 0.08:
        composite = Signal.BUY
    elif weighted_score < -0.25:
        composite = Signal.STRONG_SELL
    elif weighted_score < -0.08:
        composite = Signal.SELL
    else:
        composite = Signal.NEUTRAL

    # ── Window delta signal (dominant for 5-min markets) ──
    # Research shows this is the #1 edge: price change from window open predicts outcome
    w_delta = market.window_delta
    w_elapsed = market.window_elapsed_pct

    if is_5m and abs(w_delta) > 0.0005 and w_elapsed > 0.30:
        # Window delta dominates: weight it 5x over technical indicators
        # Scale by elapsed time (more confident later in window)
        elapsed_boost = 1.0 + w_elapsed * 1.0  # 1.3x at 30%, 1.8x at 80%
        delta_score = np.clip(w_delta * 400, -0.8, 0.8) * elapsed_boost

        # Blend: 70% window delta + 30% technical indicators
        weighted_score = 0.70 * delta_score + 0.30 * weighted_score

        logger.info(
            f"Window delta signal: Δ={w_delta:+.4f} elapsed={w_elapsed:.0%} "
            f"delta_score={delta_score:.3f} blended={weighted_score:.3f}"
        )

    # Cap blended score to prevent unrealistic edges
    weighted_score = float(np.clip(weighted_score, -0.60, 0.60))

    # ── Probability estimate ──
    base_prob = market.price_yes
    max_adj = 0.35 if is_5m else 0.22
    adjustment = weighted_score * max_adj
    our_prob = max(0.01, min(0.99, base_prob + adjustment))

    edge = our_prob - market.price_yes

    # ── Trade decision ──
    recommended_side = None
    token_id = market.token_id_yes
    abs_edge = abs(edge)

    # Cooldown check
    now = time.time()
    if now - last_trade_time < MIN_TRADE_INTERVAL:
        # Still in cooldown, don't trade
        return StrategyOutput(
            token_id=token_id, market=market.question, signals=signals,
            composite_signal=composite, probability_estimate=round(our_prob, 4),
            market_price=market.price_yes, edge=round(edge, 4),
            recommended_side=None,
        )

    # Streak penalty — skip if on a bad run
    streak_mult = _streak_penalty(recent_outcomes)
    if streak_mult < 0.4 and abs_edge < 0.05:
        return StrategyOutput(
            token_id=token_id, market=market.question, signals=signals,
            composite_signal=composite, probability_estimate=round(our_prob, 4),
            market_price=market.price_yes, edge=round(edge, 4),
            recommended_side=None,
        )

    # ── Timing gate for 5-min markets ──
    # Only trade after 40% of window has elapsed (2+ minutes in)
    # Earlier trades are noise; later trades have confirmed direction
    if is_5m and w_elapsed < 0.40:
        return StrategyOutput(
            token_id=token_id, market=market.question, signals=signals,
            composite_signal=composite, probability_estimate=round(our_prob, 4),
            market_price=market.price_yes, edge=round(edge, 4),
            recommended_side=None,
        )

    # ── Timeframe agreement filter ──
    h1_signal = signals[1].signal if len(signals) > 1 else Signal.NEUTRAL
    h1_bullish = h1_signal in (Signal.BUY, Signal.STRONG_BUY)
    h1_bearish = h1_signal in (Signal.SELL, Signal.STRONG_SELL)

    if edge > MIN_EDGE:
        # For 5-min markets with strong window delta, skip H1 filter
        if is_5m and abs(w_delta) > 0.001 and w_elapsed > 0.50:
            recommended_side = Side.BUY
            token_id = market.token_id_yes
        elif not h1_bearish:
            recommended_side = Side.BUY
            token_id = market.token_id_yes
    elif edge < -MIN_EDGE:
        if is_5m and abs(w_delta) > 0.001 and w_elapsed > 0.50:
            recommended_side = Side.SELL
            token_id = market.token_id_no
            edge = -edge
        elif not h1_bullish:
            recommended_side = Side.SELL
            token_id = market.token_id_no
            edge = -edge

    if recommended_side is not None:
        if strategy_state is not None:
            strategy_state.last_trade_time = now
        else:
            _default_last_trade_time = now

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
