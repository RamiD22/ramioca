import { motion } from "framer-motion"
import {
  DollarSign, TrendingUp, TrendingDown, Activity,
  Target, BarChart3, Shield, Zap,
} from "lucide-react"
import type { PortfolioMetrics } from "../types"

interface Props {
  metrics: PortfolioMetrics
  compact?: boolean
}

interface CardData {
  label: string
  value: string
  icon: React.ReactNode
  glow: string
  color: string
  sub?: React.ReactNode
}

function fmt(n: number, decimals = 2): string {
  return n.toLocaleString("en-US", {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  })
}

export default function MetricCards({ metrics, compact = false }: Props) {
  const pnlPositive = metrics.total_pnl >= 0

  const cards: CardData[] = compact
    ? [
        {
          label: "PnL",
          value: `${pnlPositive ? "+" : ""}$${fmt(metrics.total_pnl)}`,
          icon: pnlPositive ? <TrendingUp className="w-4 h-4" /> : <TrendingDown className="w-4 h-4" />,
          glow: pnlPositive ? "glass-card-glow-green" : "glass-card-glow-magenta",
          color: pnlPositive ? "neon-text-green" : "neon-text-red",
          sub: (
            <span className="flex items-center gap-1">
              {pnlPositive
                ? <TrendingUp className="w-3 h-3 text-neon-green/50" />
                : <TrendingDown className="w-3 h-3 text-neon-red/50" />
              }
              {`${pnlPositive ? "+" : ""}${fmt(metrics.total_pnl_pct)}%`}
            </span>
          ),
        },
        {
          label: "Win Rate",
          value: `${(metrics.win_rate * 100).toFixed(1)}%`,
          icon: <Target className="w-4 h-4" />,
          glow: "glass-card-glow-cyan",
          color: "neon-text-cyan",
          sub: `${metrics.winning_trades}W / ${metrics.losing_trades}L`,
        },
        {
          label: "Trades",
          value: `${metrics.total_trades}`,
          icon: <Activity className="w-4 h-4" />,
          glow: "glass-card",
          color: "text-white",
        },
        {
          label: "Exposure",
          value: `$${fmt(metrics.total_exposure)}`,
          icon: <BarChart3 className="w-4 h-4" />,
          glow: "glass-card",
          color: "text-white/80",
          sub: `${metrics.active_positions} pos`,
        },
      ]
    : [
        {
          label: "Balance",
          value: `$${fmt(metrics.balance)}`,
          icon: <DollarSign className="w-5 h-5" />,
          glow: "glass-card-glow-cyan",
          color: "neon-text-cyan",
        },
        {
          label: "Total PnL",
          value: `${pnlPositive ? "+" : ""}$${fmt(metrics.total_pnl)}`,
          icon: pnlPositive ? <TrendingUp className="w-5 h-5" /> : <TrendingDown className="w-5 h-5" />,
          glow: pnlPositive ? "glass-card-glow-green" : "glass-card-glow-magenta",
          color: pnlPositive ? "neon-text-green" : "neon-text-red",
          sub: (
            <span className="flex items-center gap-1">
              {pnlPositive
                ? <TrendingUp className="w-3 h-3 text-neon-green/50" />
                : <TrendingDown className="w-3 h-3 text-neon-red/50" />
              }
              {`${pnlPositive ? "+" : ""}${fmt(metrics.total_pnl_pct)}%`}
            </span>
          ),
        },
        {
          label: "Win Rate",
          value: `${(metrics.win_rate * 100).toFixed(1)}%`,
          icon: <Target className="w-5 h-5" />,
          glow: "glass-card-glow-cyan",
          color: "neon-text-cyan",
          sub: `${metrics.winning_trades}W / ${metrics.losing_trades}L`,
        },
        {
          label: "Total Trades",
          value: `${metrics.total_trades}`,
          icon: <Activity className="w-5 h-5" />,
          glow: "glass-card",
          color: "text-white",
        },
        {
          label: "Exposure",
          value: `$${fmt(metrics.total_exposure)}`,
          icon: <BarChart3 className="w-5 h-5" />,
          glow: "glass-card-glow-magenta",
          color: "neon-text-magenta",
          sub: `${metrics.active_positions} positions`,
        },
        {
          label: "Sharpe Ratio",
          value: fmt(metrics.sharpe_ratio, 3),
          icon: <Zap className="w-5 h-5" />,
          glow: "glass-card",
          color: metrics.sharpe_ratio > 1 ? "neon-text-green" : "text-white",
        },
        {
          label: "Max Drawdown",
          value: `$${fmt(metrics.max_drawdown)}`,
          icon: <Shield className="w-5 h-5" />,
          glow: "glass-card",
          color: metrics.max_drawdown > 0 ? "neon-text-red" : "text-white",
        },
        {
          label: "Avg Trade PnL",
          value: `$${fmt(metrics.avg_trade_pnl)}`,
          icon: <BarChart3 className="w-5 h-5" />,
          glow: "glass-card",
          color: metrics.avg_trade_pnl >= 0 ? "neon-text-green" : "neon-text-red",
        },
      ]

  return (
    <div className={compact ? "grid grid-cols-2 gap-2" : "grid grid-cols-2 md:grid-cols-4 gap-4"}>
      {cards.map((card, i) => (
        <motion.div
          key={card.label}
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: i * 0.05, duration: 0.4 }}
          className={`${card.glow} ${compact ? "p-3" : "p-5"}`}
        >
          <div className="flex items-center justify-between mb-2">
            <span className={`${compact ? "text-[9px]" : "text-xs"} font-medium tracking-widest uppercase text-white/40`}>
              {card.label}
            </span>
            <span className="text-white/20">{card.icon}</span>
          </div>
          <motion.div
            key={card.value}
            initial={{ opacity: 0.6, y: 2 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.25 }}
            className={`${compact ? "text-lg" : "text-2xl"} font-bold font-mono ${card.color}`}
          >
            {card.value}
          </motion.div>
          {card.sub && (
            <div className={`${compact ? "text-[9px]" : "text-xs"} text-white/30 mt-1 font-mono`}>{card.sub}</div>
          )}
        </motion.div>
      ))}
    </div>
  )
}
