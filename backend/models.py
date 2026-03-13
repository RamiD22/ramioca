from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional
from pydantic import BaseModel


class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class Timeframe(str, Enum):
    M5 = "5m"
    H1 = "1h"
    H4 = "4h"


class Signal(str, Enum):
    STRONG_BUY = "STRONG_BUY"
    BUY = "BUY"
    NEUTRAL = "NEUTRAL"
    SELL = "SELL"
    STRONG_SELL = "STRONG_SELL"


class MarketInfo(BaseModel):
    condition_id: str
    question: str
    slug: str
    token_id_yes: str
    token_id_no: str
    price_yes: float
    price_no: float
    volume: float
    liquidity: float
    end_date: Optional[str] = None
    category: str = "crypto"
    # Window context for 5-min markets (set by bot loop before strategy call)
    window_delta: float = 0.0  # (current_price - window_open) / window_open
    window_elapsed_pct: float = 0.0  # 0.0=window start, 1.0=window end


class Position(BaseModel):
    market: str
    condition_id: str
    token_id: str
    side: str
    size: float
    avg_price: float
    current_price: float
    pnl: float
    pnl_pct: float
    agent: str = ""  # "alpha" | "beta" | "" for unassigned


class TradeRecord(BaseModel):
    id: str
    timestamp: datetime
    market: str
    side: Side
    price: float
    size: float
    token_id: str
    status: str = "filled"
    pnl: Optional[float] = None
    agent: str = ""  # "alpha" | "beta"


class TimeframeSignal(BaseModel):
    timeframe: Timeframe
    signal: Signal
    confidence: float
    price: float
    sma_short: float
    sma_long: float
    rsi: float
    momentum: float


class StrategyOutput(BaseModel):
    token_id: str
    market: str
    signals: list[TimeframeSignal]
    composite_signal: Signal
    probability_estimate: float
    market_price: float
    edge: float
    recommended_side: Optional[Side] = None
    recommended_size: float = 0.0


class PortfolioMetrics(BaseModel):
    balance: float = 0.0
    total_pnl: float = 0.0
    total_pnl_pct: float = 0.0
    win_rate: float = 0.0
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    active_positions: int = 0
    total_exposure: float = 0.0
    sharpe_ratio: float = 0.0
    max_drawdown: float = 0.0
    avg_trade_pnl: float = 0.0


class BotStatus(BaseModel):
    running: bool = False
    dry_run: bool = True
    uptime_seconds: float = 0.0
    last_scan: Optional[datetime] = None
    markets_tracked: int = 0
    errors: list[str] = []


class ActivityEvent(BaseModel):
    """A single bot activity/decision event shown in the dashboard log."""
    id: str
    timestamp: datetime
    event_type: str  # "scan", "signal", "trade", "skip", "stop_loss", "take_profit", "error", "info"
    title: str  # short headline
    detail: str = ""  # extra context
    market: str = ""
    icon: str = "info"  # "scan", "signal", "trade_buy", "trade_sell", "skip", "stop", "profit", "error"
    severity: str = "info"  # "info", "success", "warning", "error"
    agent: str = ""  # "alpha" | "beta" | "" for shared events


class DashboardState(BaseModel):
    """Per-agent state container (used internally by each TradingAgent)."""
    metrics: PortfolioMetrics = PortfolioMetrics()
    positions: list[Position] = []
    recent_trades: list[TradeRecord] = []
    signals: list[StrategyOutput] = []
    bot_status: BotStatus = BotStatus()
    markets: list[MarketInfo] = []
    activity_log: list[ActivityEvent] = []


class AgentState(BaseModel):
    """Serialized state for a single agent in the competition dashboard."""
    agent_id: str  # "alpha" | "beta"
    label: str  # "Alpha (v2)" | "Beta (v1)"
    metrics: PortfolioMetrics = PortfolioMetrics()
    positions: list[Position] = []
    recent_trades: list[TradeRecord] = []
    signals: list[StrategyOutput] = []
    activity_log: list[ActivityEvent] = []


class CompetitionState(BaseModel):
    """Top-level state for dual-agent competition dashboard."""
    alpha: AgentState
    beta: AgentState
    bot_status: BotStatus = BotStatus()
    markets: list[MarketInfo] = []
    polymarket_trades: list[dict] = []
