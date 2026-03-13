import { motion } from "framer-motion"
import { ArrowUpRight, ArrowDownRight, ExternalLink } from "lucide-react"
import type { PolymarketTrade } from "../types"

interface Props {
  trades: PolymarketTrade[]
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

export default function PolymarketTrades({ trades }: Props) {
  const shown = trades.slice(0, 50)

  // Stats
  const buys = shown.filter(t => t.side === "BUY")
  const sells = shown.filter(t => t.side === "SELL")
  const totalVolume = shown.reduce((sum, t) => sum + parseFloat(t.size || "0"), 0)

  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay: 0.5, duration: 0.5 }}
      className="glass-card p-5"
    >
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <h2 className="text-sm font-medium tracking-widest uppercase text-white/40">
            Polymarket Trades
          </h2>
          <span className="text-[9px] font-mono text-white/25 bg-white/5 px-1.5 py-0.5 rounded-full">
            {trades.length}
          </span>
          <span className="text-[9px] font-mono text-neon-cyan/40 bg-neon-cyan/5 px-1.5 py-0.5 rounded-full">
            LIVE
          </span>
        </div>
        <div className="flex items-center gap-3 text-[9px] font-mono">
          <span className="text-neon-green/50">{buys.length} buys</span>
          <span className="text-neon-red/50">{sells.length} sells</span>
          <span className="text-white/30">${totalVolume.toFixed(2)} vol</span>
        </div>
      </div>

      {shown.length === 0 ? (
        <div className="text-white/20 text-sm font-mono text-center py-6">
          No Polymarket trades yet
        </div>
      ) : (
        <div className="overflow-y-auto max-h-[400px] scrollbar-thin">
          <table className="w-full text-xs">
            <thead className="sticky top-0 bg-[#0a0a1a]/90 backdrop-blur z-10">
              <tr className="text-white/30 text-[9px] uppercase tracking-wider">
                <th className="text-left pb-2 font-medium">Market</th>
                <th className="text-center pb-2 font-medium">Side</th>
                <th className="text-right pb-2 font-medium">Size</th>
                <th className="text-right pb-2 font-medium">Price</th>
                <th className="text-right pb-2 font-medium">Status</th>
                <th className="text-right pb-2 font-medium">Tx</th>
              </tr>
            </thead>
            <tbody className="font-mono">
              {shown.map((trade, i) => {
                const isBuy = trade.side === "BUY"
                const size = parseFloat(trade.size || "0")
                const price = parseFloat(trade.price || "0")
                const matchTime = trade.match_time || trade.created_at

                return (
                  <motion.tr
                    key={trade.id || i}
                    initial={{ opacity: 0, x: -10 }}
                    animate={{ opacity: 1, x: 0 }}
                    transition={{ delay: i * 0.015 }}
                    className="border-t border-white/[0.04] hover:bg-white/[0.04] transition-all duration-150"
                  >
                    {/* Market */}
                    <td className="py-2 pr-2 text-white/60 max-w-[200px] truncate">
                      <span className="text-white/40">{matchTime ? timeAgo(matchTime) : ""}</span>
                      {" "}
                      {(trade.title || trade.market || "").slice(0, 40)}
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
                      ${size.toFixed(2)}
                    </td>

                    {/* Price */}
                    <td className="py-2 text-right text-white/50">
                      {price.toFixed(3)}
                    </td>

                    {/* Status */}
                    <td className="py-2 text-right">
                      <span className={`text-[9px] ${
                        trade.status === "MATCHED" ? "text-neon-green/60" :
                        trade.status === "CONFIRMED" ? "text-neon-cyan/60" :
                        "text-white/30"
                      }`}>
                        {trade.status || "—"}
                      </span>
                    </td>

                    {/* Tx link */}
                    <td className="py-2 text-right">
                      {trade.transaction_hash ? (
                        <a
                          href={`https://polygonscan.com/tx/${trade.transaction_hash}`}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="text-neon-cyan/40 hover:text-neon-cyan/80 transition-colors"
                        >
                          <ExternalLink className="w-3 h-3 inline" />
                        </a>
                      ) : (
                        <span className="text-white/15">—</span>
                      )}
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
