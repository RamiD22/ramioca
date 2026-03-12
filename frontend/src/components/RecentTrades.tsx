import { motion } from "framer-motion"
import { ArrowUpRight, ArrowDownRight, Clock, CheckCircle, AlertCircle, Loader2 } from "lucide-react"
import type { TradeRecord } from "../types"

interface Props {
  trades: TradeRecord[]
  compact?: boolean
}

function timeAgo(ts: string): string {
  const diff = Date.now() - new Date(ts).getTime()
  const mins = Math.floor(diff / 60_000)
  if (mins < 1) return "just now"
  if (mins < 60) return `${mins}m ago`
  const hrs = Math.floor(mins / 60)
  if (hrs < 24) return `${hrs}h ago`
  return `${Math.floor(hrs / 24)}d ago`
}

function statusIcon(status: string) {
  switch (status) {
    case "settled":
      return <CheckCircle className="w-3 h-3 text-white/30" />
    case "filled":
      return <Loader2 className="w-3 h-3 text-neon-cyan/50 animate-spin" />
    case "placed":
      return <Clock className="w-3 h-3 text-yellow-400/50" />
    default:
      return <AlertCircle className="w-3 h-3 text-white/20" />
  }
}

function statusColor(status: string): string {
  switch (status) {
    case "settled": return "text-white/40"
    case "filled": return "text-neon-cyan/60"
    case "placed": return "text-yellow-400/60"
    default: return "text-white/30"
  }
}

export default function RecentTrades({ trades, compact = false }: Props) {
  const shown = compact ? trades.slice(0, 15) : trades.slice(0, 30)

  // Compute summary stats for resolved trades
  const resolved = trades.filter(t => t.pnl !== null && t.pnl !== undefined)
  const wins = resolved.filter(t => t.pnl! > 0)
  const losses = resolved.filter(t => t.pnl! < 0)
  const pending = trades.filter(t => t.pnl === null || t.pnl === undefined)

  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay: 0.4, duration: 0.5 }}
      className={`glass-card ${compact ? "p-3" : "p-5"}`}
    >
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <h2 className={`${compact ? "text-xs" : "text-sm"} font-medium tracking-widest uppercase text-white/40`}>
            Recent Trades
          </h2>
          <span className="text-[9px] font-mono text-white/25 bg-white/5 px-1.5 py-0.5 rounded-full">
            {trades.length}
          </span>
        </div>
        {/* Mini status chips */}
        <div className="flex items-center gap-2 text-[9px] font-mono">
          <span className="text-neon-green/50">{wins.length}W</span>
          <span className="text-neon-red/50">{losses.length}L</span>
          {pending.length > 0 && (
            <span className="text-yellow-400/50">{pending.length} pending</span>
          )}
        </div>
      </div>

      {shown.length === 0 ? (
        <div className={`text-white/20 ${compact ? "text-xs" : "text-sm"} font-mono text-center py-4`}>
          No trades yet
        </div>
      ) : (
        <div className={`overflow-y-auto ${compact ? "max-h-[280px]" : "max-h-[400px]"} scrollbar-thin`}>
          <table className={`w-full ${compact ? "text-[11px]" : "text-xs"}`}>
            <thead className="sticky top-0 bg-[#0a0a1a]/90 backdrop-blur z-10">
              <tr className="text-white/30 text-[9px] uppercase tracking-wider">
                <th className="text-left pb-2 font-medium">Market</th>
                <th className="text-center pb-2 font-medium">Side</th>
                <th className="text-right pb-2 font-medium">Size</th>
                <th className="text-right pb-2 font-medium">Price</th>
                <th className="text-right pb-2 font-medium">PnL</th>
                <th className="text-right pb-2 font-medium">Status</th>
              </tr>
            </thead>
            <tbody className="font-mono">
              {shown.map((trade, i) => {
                const isBuy = trade.side === "BUY"
                const hasPnl = trade.pnl !== null && trade.pnl !== undefined
                const isWin = hasPnl && trade.pnl! > 0
                const isLoss = hasPnl && trade.pnl! < 0

                return (
                  <motion.tr
                    key={trade.id || i}
                    initial={{ opacity: 0, x: -10 }}
                    animate={{ opacity: 1, x: 0 }}
                    transition={{ delay: i * 0.02 }}
                    className="border-t border-white/[0.04] hover:bg-white/[0.04] transition-all duration-150"
                  >
                    {/* Market */}
                    <td className={`py-2 pr-2 text-white/60 ${compact ? "max-w-[100px]" : "max-w-[150px]"} truncate`}>
                      <span className="text-white/40">{timeAgo(trade.timestamp)}</span>
                      {" "}
                      {trade.market.replace(/Up or Down - /, "").replace(/ ET$/, "").slice(0, compact ? 20 : 35)}
                    </td>

                    {/* Side */}
                    <td className="py-2 text-center">
                      <span className={`inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded text-[10px] font-semibold ${
                        isBuy
                          ? "bg-neon-green/10 text-neon-green border border-neon-green/20"
                          : "bg-neon-red/10 text-neon-red border border-neon-red/20"
                      }`}>
                        {isBuy
                          ? <ArrowUpRight className="w-2.5 h-2.5" />
                          : <ArrowDownRight className="w-2.5 h-2.5" />
                        }
                        {trade.side}
                      </span>
                    </td>

                    {/* Size */}
                    <td className="py-2 text-right text-white/60">
                      ${trade.size.toFixed(0)}
                    </td>

                    {/* Price */}
                    <td className="py-2 text-right text-white/50">
                      {trade.price.toFixed(2)}
                    </td>

                    {/* PnL */}
                    <td className="py-2 text-right">
                      {hasPnl ? (
                        <span className={`font-semibold ${
                          isWin ? "text-neon-green" : isLoss ? "text-neon-red" : "text-white/40"
                        }`}>
                          {isWin ? "+" : ""}{trade.pnl!.toFixed(2)}
                        </span>
                      ) : (
                        <span className="text-white/20">—</span>
                      )}
                    </td>

                    {/* Status */}
                    <td className="py-2 text-right">
                      <span className={`inline-flex items-center gap-1 ${statusColor(trade.status)}`}>
                        {statusIcon(trade.status)}
                        <span className="text-[9px]">{trade.status}</span>
                      </span>
                    </td>
                  </motion.tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </motion.div>
  )
}
