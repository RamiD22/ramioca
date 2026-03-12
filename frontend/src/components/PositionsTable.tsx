import { motion } from "framer-motion"
import type { Position } from "../types"

interface Props {
  positions: Position[]
  compact?: boolean
}

export default function PositionsTable({ positions, compact = false }: Props) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay: 0.35, duration: 0.5 }}
      className={`glass-card ${compact ? "p-3" : "p-5"}`}
    >
      <div className="flex items-center gap-2 mb-3">
        <h2 className={`${compact ? "text-xs" : "text-sm"} font-medium tracking-widest uppercase text-white/40`}>
          Open Positions
        </h2>
        {positions.length > 0 && (
          <span className="text-[9px] font-mono text-white/25 bg-white/5 px-1.5 py-0.5 rounded-full">
            {positions.length}
          </span>
        )}
      </div>

      {positions.length === 0 ? (
        <div className={`text-white/20 ${compact ? "text-xs" : "text-sm"} font-mono text-center py-4`}>
          No open positions
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className={`w-full ${compact ? "text-xs" : "text-sm"}`}>
            <thead>
              <tr className="text-white/30 text-[10px] uppercase tracking-wider">
                <th className="text-left pb-2 font-medium">Market</th>
                <th className="text-right pb-2 font-medium">Side</th>
                <th className="text-right pb-2 font-medium">Size</th>
                {!compact && <th className="text-right pb-2 font-medium">Entry</th>}
                {!compact && <th className="text-right pb-2 font-medium">Current</th>}
                <th className="text-right pb-2 font-medium">PnL</th>
              </tr>
            </thead>
            <tbody className="font-mono">
              {positions.map((pos, i) => (
                <motion.tr
                  key={pos.condition_id || i}
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                  transition={{ delay: i * 0.05 }}
                  className="border-t border-white/[0.04] hover:bg-white/[0.04] hover:border-white/[0.08] transition-all duration-200"
                >
                  <td className={`py-2.5 pr-2 text-white/70 ${compact ? "max-w-[120px]" : "max-w-[200px]"} truncate`}>
                    {pos.market}
                  </td>
                  <td className={`py-2.5 text-right font-semibold ${
                    pos.side === "YES" || pos.side === "BUY" || pos.side === "Up" ? "text-neon-green" : "text-neon-red"
                  }`}>
                    {pos.side}
                  </td>
                  <td className="py-2.5 text-right text-white/60">
                    {pos.size.toFixed(2)}
                  </td>
                  {!compact && (
                    <td className="py-2.5 text-right text-white/60">
                      {pos.avg_price.toFixed(4)}
                    </td>
                  )}
                  {!compact && (
                    <td className="py-2.5 text-right text-white/80">
                      <div className="flex items-center justify-end gap-1.5">
                        <span className={`inline-block w-1.5 h-1.5 rounded-full ${
                          pos.current_price >= pos.avg_price ? "pulse-dot-green" : "pulse-dot-red"
                        }`} style={{ width: "0.375rem", height: "0.375rem" }} />
                        {pos.current_price.toFixed(4)}
                      </div>
                    </td>
                  )}
                  <td className="py-2.5 text-right">
                    <div className="flex flex-col items-end gap-1">
                      <span className={`font-semibold ${
                        pos.pnl >= 0 ? "text-neon-green" : "text-neon-red"
                      }`}>
                        {pos.pnl >= 0 ? "+" : ""}${pos.pnl.toFixed(2)}
                        {!compact && (
                          <span className="text-[10px] text-white/30 ml-1">
                            ({pos.pnl_pct >= 0 ? "+" : ""}{pos.pnl_pct.toFixed(1)}%)
                          </span>
                        )}
                      </span>
                      {/* Mini PnL bar */}
                      <div className="w-14 h-1 rounded-full bg-white/5 overflow-hidden">
                        <motion.div
                          initial={{ width: 0 }}
                          animate={{ width: `${Math.min(Math.abs(pos.pnl_pct), 100)}%` }}
                          transition={{ duration: 0.5, ease: "easeOut" }}
                          className={`h-full rounded-full ${pos.pnl >= 0 ? "bg-neon-green/60" : "bg-neon-red/60"}`}
                          style={{
                            boxShadow: pos.pnl >= 0
                              ? "0 0 4px rgba(57,255,20,0.3)"
                              : "0 0 4px rgba(255,49,49,0.3)",
                          }}
                        />
                      </div>
                    </div>
                  </td>
                </motion.tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </motion.div>
  )
}
