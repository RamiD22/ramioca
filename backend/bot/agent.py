"""Trading agent abstraction for dual-agent competition.

Each TradingAgent encapsulates:
  - Strategy function (v1 or v2)
  - Per-agent mutable state (cooldown, streak tracking)
  - Executor instance
  - Virtual budget and position ownership
"""

from __future__ import annotations

import logging
from collections import deque
from typing import TYPE_CHECKING, Callable, Literal

from backend.models import DashboardState, StrategyOutput, MarketInfo, Timeframe

if TYPE_CHECKING:
    from backend.bot.executor import Executor

logger = logging.getLogger(__name__)

AgentId = Literal["alpha", "beta"]


class StrategyState:
    """Mutable per-agent state for a strategy instance."""

    def __init__(self) -> None:
        self.last_trade_time: float = 0.0
        self.recent_outcomes: deque[bool] = deque(maxlen=20)
        # Reference to parent agent's DashboardState (set by TradingAgent.__init__).
        # Used by Claude strategy to access trades, metrics, positions.
        self._agent_state: DashboardState | None = None


class TradingAgent:
    """Encapsulates all per-agent state for the competition."""

    def __init__(
        self,
        agent_id: AgentId,
        label: str,
        strategy_fn: Callable[
            [MarketInfo, dict[Timeframe, list[float]], StrategyState],
            StrategyOutput,
        ],
        budget: float,
    ) -> None:
        self.agent_id = agent_id
        self.label = label
        self.strategy_fn = strategy_fn
        self.budget = budget

        # Per-agent dashboard state (isolated)
        self.state = DashboardState()
        self.state.metrics.balance = budget

        # Per-agent strategy state (cooldown, streaks)
        self.strategy_state = StrategyState()
        self.strategy_state._agent_state = self.state

        # Per-agent executor (set after init)
        self.executor: Executor | None = None

        # Per-agent PnL baseline (set on first metrics computation)
        self.pnl_baseline: float | None = None

        # Position ownership — token_ids that this agent has traded
        self.owned_token_ids: set[str] = set()
        # Per-token size tracking — how many $ this agent invested per token
        self.owned_sizes: dict[str, float] = {}  # token_id → total USDC invested

    def __repr__(self) -> str:
        return f"TradingAgent({self.agent_id!r}, budget=${self.budget:.0f})"
