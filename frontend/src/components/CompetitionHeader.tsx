import { motion } from "framer-motion"
import { Trophy, Swords } from "lucide-react"
import type { AgentState } from "../types"

interface Props {
  alpha: AgentState
  beta: AgentState
}

function fmt(n: number): string {
  return n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

export default function CompetitionHeader({ alpha, beta }: Props) {
  const alphaPnl = alpha.metrics.total_pnl
  const betaPnl = beta.metrics.total_pnl
  const leader = alphaPnl > betaPnl ? "alpha" : alphaPnl < betaPnl ? "beta" : "tied"
  const diff = Math.abs(alphaPnl - betaPnl)

  return (
    <motion.div
      initial={{ opacity: 0, y: -10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay: 0.1, duration: 0.5 }}
      className="glass-card p-5 mb-6"
    >
      <div className="flex items-center justify-center gap-6 md:gap-12">
        {/* Alpha */}
        <div className={`text-center transition-all duration-500 ${leader === "alpha" ? "scale-110" : "opacity-60"}`}>
          <div className="flex items-center justify-center gap-2 mb-1">
            {leader === "alpha" && <Trophy className="w-4 h-4 text-neon-cyan" />}
            <span className="text-sm font-bold tracking-wide neon-text-cyan">
              {alpha.label}
            </span>
            {leader === "alpha" && (
              <motion.span
                layoutId="leader-badge"
                className="text-[7px] font-mono font-bold uppercase px-1.5 py-0.5 rounded-full bg-neon-cyan/15 text-neon-cyan/80 border border-neon-cyan/20"
              >
                LEADING
              </motion.span>
            )}
          </div>
          <motion.div
            key={`alpha-${fmt(alphaPnl)}`}
            initial={{ scale: 0.98 }}
            animate={{ scale: 1 }}
            className={`text-2xl md:text-3xl font-bold font-mono ${
              alphaPnl >= 0 ? "neon-text-green" : "neon-text-red"
            }`}
          >
            {alphaPnl >= 0 ? "+" : ""}${fmt(alphaPnl)}
          </motion.div>
          <div className="flex items-center justify-center gap-3 mt-1.5 text-[10px] font-mono text-white/30">
            <span>{(alpha.metrics.win_rate * 100).toFixed(0)}% WR</span>
            <span className="text-white/10">|</span>
            <span>{alpha.metrics.total_trades} trades</span>
          </div>
        </div>

        {/* VS */}
        <div className="flex flex-col items-center gap-1">
          <Swords className="w-5 h-5 text-white/15" />
          <span className="text-xs font-bold text-white/10 tracking-widest">VS</span>
          {diff > 0.01 && (
            <motion.div
              key={diff.toFixed(2)}
              initial={{ scale: 0.8, opacity: 0 }}
              animate={{ scale: 1, opacity: 1 }}
              className="text-[9px] font-mono text-white/20 mt-0.5"
            >
              {`\u0394 $${fmt(diff)}`}
            </motion.div>
          )}
        </div>

        {/* Beta */}
        <div className={`text-center transition-all duration-500 ${leader === "beta" ? "scale-110" : "opacity-60"}`}>
          <div className="flex items-center justify-center gap-2 mb-1">
            {leader === "beta" && <Trophy className="w-4 h-4 text-neon-magenta" />}
            <span className="text-sm font-bold tracking-wide neon-text-magenta">
              {beta.label}
            </span>
            {leader === "beta" && (
              <motion.span
                layoutId="leader-badge"
                className="text-[7px] font-mono font-bold uppercase px-1.5 py-0.5 rounded-full bg-neon-magenta/15 text-neon-magenta/80 border border-neon-magenta/20"
              >
                LEADING
              </motion.span>
            )}
          </div>
          <motion.div
            key={`beta-${fmt(betaPnl)}`}
            initial={{ scale: 0.98 }}
            animate={{ scale: 1 }}
            className={`text-2xl md:text-3xl font-bold font-mono ${
              betaPnl >= 0 ? "neon-text-green" : "neon-text-red"
            }`}
          >
            {betaPnl >= 0 ? "+" : ""}${fmt(betaPnl)}
          </motion.div>
          <div className="flex items-center justify-center gap-3 mt-1.5 text-[10px] font-mono text-white/30">
            <span>{(beta.metrics.win_rate * 100).toFixed(0)}% WR</span>
            <span className="text-white/10">|</span>
            <span>{beta.metrics.total_trades} trades</span>
          </div>
        </div>
      </div>
    </motion.div>
  )
}
