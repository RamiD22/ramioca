import { motion } from "framer-motion"
import MetricCards from "./MetricCards"
import PnLChart from "./PnLChart"
import ActivityLog from "./ActivityLog"
import PositionsTable from "./PositionsTable"
import type { AgentState, PnLSnapshot } from "../types"

interface Props {
  agent: AgentState
  color: "cyan" | "magenta"
  delay?: number
  pnlHistory?: PnLSnapshot[]
}

export default function AgentPanel({ agent, color, delay = 0, pnlHistory }: Props) {
  const gradientId = `pnl-${agent.agent_id}`
  const strokeColor = color === "cyan" ? "#00f0ff" : "#ff00e5"
  const borderColor = color === "cyan" ? "border-neon-cyan/20" : "border-neon-magenta/20"
  const textColor = color === "cyan" ? "neon-text-cyan" : "neon-text-magenta"

  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay, duration: 0.5 }}
      className="space-y-4"
    >
      {/* Agent header */}
      <div className={`flex items-center justify-between border-b ${borderColor} pb-2`}>
        <h2 className={`text-sm font-bold tracking-widest uppercase ${textColor}`}>
          {agent.label}
        </h2>
        <span className={`text-xs font-mono ${
          agent.metrics.total_pnl >= 0 ? "neon-text-green" : "neon-text-red"
        }`}>
          {agent.metrics.total_pnl >= 0 ? "+" : ""}${agent.metrics.total_pnl.toFixed(2)}
        </span>
      </div>

      {/* Compact Metrics */}
      <MetricCards metrics={agent.metrics} compact />

      {/* PnL Chart */}
      <PnLChart
        trades={agent.recent_trades}
        history={pnlHistory}
        gradientId={gradientId}
        strokeColor={strokeColor}
      />

      {/* Positions */}
      <PositionsTable positions={agent.positions} compact />

      {/* Activity Log */}
      <ActivityLog events={agent.activity_log || []} compact />
    </motion.div>
  )
}
