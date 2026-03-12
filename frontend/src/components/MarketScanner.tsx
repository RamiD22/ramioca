import { motion } from "framer-motion"
import { Radio } from "lucide-react"
import type { StrategyOutput } from "../types"

interface Props {
  signals: StrategyOutput[]
}

const SIGNAL_COLORS: Record<string, string> = {
  STRONG_BUY: "bg-neon-green/20 text-neon-green border-neon-green/30",
  BUY: "bg-neon-green/10 text-neon-green/70 border-neon-green/20",
  NEUTRAL: "bg-white/5 text-white/40 border-white/10",
  SELL: "bg-neon-red/10 text-neon-red/70 border-neon-red/20",
  STRONG_SELL: "bg-neon-red/20 text-neon-red border-neon-red/30",
}

export default function MarketScanner({ signals }: Props) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay: 0.4, duration: 0.5 }}
      className="glass-card-glow-cyan p-5"
    >
      <div className="flex items-center gap-2 mb-4">
        <Radio className="w-4 h-4 text-neon-cyan" />
        <h2 className="text-sm font-medium tracking-widest uppercase text-white/40">
          Signal Scanner
        </h2>
      </div>

      {signals.length === 0 ? (
        <div className="text-white/20 text-sm font-mono text-center py-8">
          Scanning markets...
        </div>
      ) : (
        <div className="space-y-3 max-h-[400px] overflow-y-auto pr-1">
          {signals.map((sig, i) => (
            <motion.div
              key={sig.token_id || i}
              initial={{ opacity: 0, x: -10 }}
              animate={{ opacity: 1, x: 0 }}
              transition={{ delay: i * 0.05 }}
              className="bg-surface-2/50 rounded-xl p-4 border border-white/[0.04]"
            >
              <div className="flex items-start justify-between gap-3 mb-3">
                <div className="text-sm text-white/70 leading-tight flex-1">
                  {sig.market.length > 60 ? sig.market.slice(0, 60) + "..." : sig.market}
                </div>
                <span className={`flex-shrink-0 text-[10px] font-mono font-bold uppercase px-2.5 py-1 rounded-full border ${
                  SIGNAL_COLORS[sig.composite_signal] || SIGNAL_COLORS.NEUTRAL
                }`}>
                  {sig.composite_signal.replace("_", " ")}
                </span>
              </div>

              <div className="grid grid-cols-3 gap-2 mb-3">
                {sig.signals.map((tf) => (
                  <div key={tf.timeframe} className="text-center">
                    <div className="text-[10px] uppercase text-white/25 mb-1">{tf.timeframe}</div>
                    <div className={`text-xs font-mono font-semibold ${
                      tf.signal.includes("BUY") ? "text-neon-green" :
                      tf.signal.includes("SELL") ? "text-neon-red" : "text-white/40"
                    }`}>
                      {tf.signal.replace("_", " ")}
                    </div>
                    <div className="text-[10px] text-white/20 font-mono">
                      RSI {tf.rsi.toFixed(0)}
                    </div>
                  </div>
                ))}
              </div>

              <div className="flex items-center justify-between text-xs font-mono">
                <div className="text-white/30">
                  Mkt: <span className="text-white/50">{(sig.market_price * 100).toFixed(1)}%</span>
                  {" | "}
                  Est: <span className="text-neon-cyan">{(sig.probability_estimate * 100).toFixed(1)}%</span>
                </div>
                <div className={`font-semibold ${
                  sig.edge > 0 ? "text-neon-green" : sig.edge < 0 ? "text-neon-red" : "text-white/30"
                }`}>
                  Edge: {sig.edge > 0 ? "+" : ""}{(sig.edge * 100).toFixed(2)}%
                </div>
              </div>
            </motion.div>
          ))}
        </div>
      )}
    </motion.div>
  )
}
