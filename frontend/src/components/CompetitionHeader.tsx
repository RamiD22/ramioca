import { motion } from "framer-motion"
import { Brain, Zap } from "lucide-react"
import type { AgentState } from "../types"

interface Props {
  alpha: AgentState
  beta?: AgentState
}

function fmt(n: number): string {
  return n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

export default function CompetitionHeader({ alpha }: Props) {
  const pnl = alpha.metrics.total_pnl
  const wr = (alpha.metrics.win_rate * 100).toFixed(0)

  return (
    <motion.div
      initial={{ opacity: 0, y: -10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay: 0.1, duration: 0.5 }}
      className="glass-card p-5 mb-6"
    >
      <div className="flex items-center justify-center gap-8">
        <div className="text-center">
          <div className="flex items-center justify-center gap-2 mb-2">
            <Brain className="w-5 h-5 text-neon-cyan" />
            <span className="text-lg font-bold tracking-wide neon-text-cyan">
              {alpha.label}
            </span>
            <Zap className="w-4 h-4 text-neon-yellow" />
          </div>
          <motion.div
            key={`pnl-${fmt(pnl)}`}
            initial={{ scale: 0.98 }}
            animate={{ scale: 1 }}
            className={`text-3xl md:text-4xl font-bold font-mono ${
              pnl >= 0 ? "neon-text-green" : "neon-text-red"
            }`}
          >
            {pnl >= 0 ? "+" : ""}${fmt(pnl)}
          </motion.div>
          <div className="flex items-center justify-center gap-4 mt-2 text-xs font-mono text-white/30">
            <span>{wr}% Win Rate</span>
            <span className="text-white/10">|</span>
            <span>{alpha.metrics.total_trades} trades</span>
            <span className="text-white/10">|</span>
            <span>{alpha.metrics.active_positions} positions</span>
            <span className="text-white/10">|</span>
            <span>${fmt(alpha.metrics.total_exposure)} exposed</span>
          </div>
        </div>
      </div>
    </motion.div>
  )
}
