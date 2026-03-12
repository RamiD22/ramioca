export interface PortfolioMetrics {
  balance: number
  total_pnl: number
  total_pnl_pct: number
  win_rate: number
  total_trades: number
  winning_trades: number
  losing_trades: number
  active_positions: number
  total_exposure: number
  sharpe_ratio: number
  max_drawdown: number
  avg_trade_pnl: number
}

export interface Position {
  market: string
  condition_id: string
  token_id: string
  side: string
  size: number
  avg_price: number
  current_price: number
  pnl: number
  pnl_pct: number
  agent: string
}

export interface TradeRecord {
  id: string
  timestamp: string
  market: string
  side: "BUY" | "SELL"
  price: number
  size: number
  token_id: string
  status: string
  pnl: number | null
  agent: string
}

export interface TimeframeSignal {
  timeframe: "5m" | "1h" | "4h"
  signal: "STRONG_BUY" | "BUY" | "NEUTRAL" | "SELL" | "STRONG_SELL"
  confidence: number
  price: number
  sma_short: number
  sma_long: number
  rsi: number
  momentum: number
}

export interface StrategyOutput {
  token_id: string
  market: string
  signals: TimeframeSignal[]
  composite_signal: string
  probability_estimate: number
  market_price: number
  edge: number
  recommended_side: "BUY" | "SELL" | null
  recommended_size: number
}

export interface MarketInfo {
  condition_id: string
  question: string
  slug: string
  token_id_yes: string
  token_id_no: string
  price_yes: number
  price_no: number
  volume: number
  liquidity: number
  end_date: string | null
  category: string
}

export interface BotStatus {
  running: boolean
  dry_run: boolean
  uptime_seconds: number
  last_scan: string | null
  markets_tracked: number
  errors: string[]
}

export interface ActivityEvent {
  id: string
  timestamp: string
  event_type: string
  title: string
  detail: string
  market: string
  icon: string
  severity: string
  agent: string
}

// ── Dual-Agent Competition ──

export interface AgentState {
  agent_id: string
  label: string
  metrics: PortfolioMetrics
  positions: Position[]
  recent_trades: TradeRecord[]
  signals: StrategyOutput[]
  activity_log: ActivityEvent[]
}

export interface PnLSnapshot {
  created_at: string
  total_pnl: number
  balance: number
  total_exposure: number
  win_rate: number
  total_trades: number
}

export interface CompetitionState {
  alpha: AgentState
  beta: AgentState
  bot_status: BotStatus
  markets: MarketInfo[]
}
