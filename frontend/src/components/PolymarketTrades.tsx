import { useState } from "react"
import { motion, AnimatePresence } from "framer-motion"
import { TrendingUp, TrendingDown, ChevronDown, ChevronRight, ExternalLink } from "lucide-react"
import type { PolymarketPosition } from "../types"

interface Props {
  trades: PolymarketPosition[]
}

function timeAgo(ts: number): string {
  const diff = Date.now() - ts * 1000
  const mins = Math.floor(diff / 60_000)
  if (mins < 1) return "just now"
  if (mins < 60) return `${mins}m ago`
  const hrs = Math.floor(mins / 60)
  if (hrs < 24) return `${hrs}h ago`
  return `${Math.floor(hrs / 24)}d ago`
}

export default function PolymarketTrades({ trades }: Props) {
  const [expanded, setExpanded] = useState<Set<number>>(new Set())

  const toggle = (i: number) => {
    setExpanded(prev => {
      const next = new Set(prev)
      next.has(i) ? next.delete(i) : next.add(i)
      return next
    })
  }

  // Stats
  const totalCost = trades.reduce((s, t) => s + t.total_cost, 0)
  const totalRev = trades.reduce((s, t) => s + t.total_revenue, 0)
  const totalPnl = trades.reduce((s, t) => s + t.pnl, 0)
  const winners = trades.filter(t => t.pnl > 0).length
  const losers = trades.filter(t => t.pnl < 0).length

  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay: 0.5, duration: 0.5 }}
      className="glass-card p-5"
    >
      {/* Header */}
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-2">
          <h2 className="text-sm font-medium tracking-widest uppercase text-white/40">
            Polymarket Trades
          </h2>
          <span className="text-[9px] font-mono text-white/25 bg-white/5 px-1.5 py-0.5 rounded-full">
            {trades.length} markets
          </span>
        </div>
        <div className="flex items-center gap-4 text-[10px] font-mono">
          <span className="text-neon-green/50">{winners}W</span>
          <span className="text-neon-red/50">{losers}L</span>
          <span className="text-white/30">${totalCost.toFixed(0)} invested</span>
          <span className={`font-semibold ${totalPnl >= 0 ? "text-neon-green" : "text-neon-red"}`}>
            {totalPnl >= 0 ? "+" : ""}${totalPnl.toFixed(2)} PnL
          </span>
        </div>
      </div>

      {trades.length === 0 ? (
        <div className="text-white/20 text-sm font-mono text-center py-6">
          No Polymarket trades yet
        </div>
      ) : (
        <div className="overflow-y-auto max-h-[500px] scrollbar-thin space-y-1">
          {trades.map((pos, i) => {
            const isOpen = expanded.has(i)
            const pnlColor = pos.pnl >= 0 ? "text-neon-green" : "text-neon-red"
            const PnlIcon = pos.pnl >= 0 ? TrendingUp : TrendingDown

            return (
              <div key={`${pos.slug}-${pos.outcome}-${i}`}>
                {/* Position row */}
                <button
                  onClick={() => toggle(i)}
                  className="w-full flex items-center gap-3 px-3 py-2.5 rounded-lg hover:bg-white/[0.04] transition-all duration-150 text-left cursor-pointer"
                >
                  {/* Expand icon */}
                  <span className="text-white/20 flex-shrink-0">
                    {isOpen ? <ChevronDown className="w-3.5 h-3.5" /> : <ChevronRight className="w-3.5 h-3.5" />}
                  </span>

                  {/* Market + outcome */}
                  <div className="flex-1 min-w-0">
                    <div className="text-xs text-white/60 truncate">
                      {pos.title}
                    </div>
                    <div className="flex items-center gap-2 mt-0.5">
                      <span className={`text-[9px] font-mono px-1.5 py-0.5 rounded ${
                        pos.outcome === "Up"
                          ? "bg-neon-green/10 text-neon-green/70"
                          : "bg-neon-red/10 text-neon-red/70"
                      }`}>
                        {pos.outcome}
                      </span>
                      <span className="text-[9px] text-white/20 font-mono">
                        {timeAgo(pos.last_trade_time)}
                      </span>
                      <span className="text-[9px] text-white/15 font-mono">
                        {pos.trade_count} trade{pos.trade_count > 1 ? "s" : ""}
                      </span>
                      <span className={`text-[9px] font-mono px-1 py-0.5 rounded ${
                        pos.status === "open"
                          ? "bg-neon-yellow/10 text-neon-yellow/60"
                          : "bg-white/5 text-white/25"
                      }`}>
                        {pos.status}
                      </span>
                    </div>
                  </div>

                  {/* Cost / Revenue */}
                  <div className="text-right flex-shrink-0 text-[10px] font-mono space-y-0.5">
                    <div className="text-white/30">
                      ${pos.total_cost.toFixed(2)} in
                    </div>
                    {pos.total_revenue > 0 && (
                      <div className="text-white/30">
                        ${pos.total_revenue.toFixed(2)} out
                      </div>
                    )}
                  </div>

                  {/* PnL */}
                  <div className={`flex items-center gap-1 flex-shrink-0 text-xs font-mono font-semibold ${pnlColor}`}>
                    <PnlIcon className="w-3.5 h-3.5" />
                    {pos.pnl >= 0 ? "+" : ""}${pos.pnl.toFixed(2)}
                  </div>
                </button>

                {/* Expanded trades */}
                <AnimatePresence>
                  {isOpen && (
                    <motion.div
                      initial={{ height: 0, opacity: 0 }}
                      animate={{ height: "auto", opacity: 1 }}
                      exit={{ height: 0, opacity: 0 }}
                      transition={{ duration: 0.2 }}
                      className="overflow-hidden"
                    >
                      <div className="ml-8 mr-3 mb-2 bg-white/[0.02] rounded-lg border border-white/[0.04]">
                        <table className="w-full text-[10px] font-mono">
                          <thead>
                            <tr className="text-white/20 text-[9px]">
                              <th className="text-left px-3 py-1.5">Side</th>
                              <th className="text-right px-3 py-1.5">Shares</th>
                              <th className="text-right px-3 py-1.5">Price</th>
                              <th className="text-right px-3 py-1.5">USDC</th>
                              <th className="text-right px-3 py-1.5">Tx</th>
                            </tr>
                          </thead>
                          <tbody>
                            {pos.trades.map((t, j) => (
                              <tr key={j} className="border-t border-white/[0.03]">
                                <td className="px-3 py-1.5">
                                  <span className={`px-1 py-0.5 rounded ${
                                    t.side === "BUY"
                                      ? "bg-neon-green/10 text-neon-green/70"
                                      : "bg-neon-red/10 text-neon-red/70"
                                  }`}>
                                    {t.side}
                                  </span>
                                </td>
                                <td className="text-right px-3 py-1.5 text-white/40">{t.shares.toFixed(1)}</td>
                                <td className="text-right px-3 py-1.5 text-white/40">{t.price.toFixed(4)}</td>
                                <td className={`text-right px-3 py-1.5 ${
                                  t.side === "BUY" ? "text-neon-red/60" : "text-neon-green/60"
                                }`}>
                                  {t.side === "BUY" ? "-" : "+"}${t.usdc.toFixed(2)}
                                </td>
                                <td className="text-right px-3 py-1.5">
                                  {t.transactionHash ? (
                                    <a
                                      href={`https://polygonscan.com/tx/${t.transactionHash}`}
                                      target="_blank"
                                      rel="noopener noreferrer"
                                      className="text-neon-cyan/30 hover:text-neon-cyan/70"
                                    >
                                      <ExternalLink className="w-3 h-3 inline" />
                                    </a>
                                  ) : "—"}
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                        {/* Position summary */}
                        <div className="flex items-center justify-between px-3 py-2 border-t border-white/[0.04] text-[9px] text-white/25">
                          <span>Avg entry: ${pos.avg_entry.toFixed(4)}</span>
                          {pos.net_shares > 0.5 && (
                            <span>{pos.net_shares.toFixed(0)} shares remaining</span>
                          )}
                        </div>
                      </div>
                    </motion.div>
                  )}
                </AnimatePresence>
              </div>
            )
          })}
        </div>
      )}
    </motion.div>
  )
}
