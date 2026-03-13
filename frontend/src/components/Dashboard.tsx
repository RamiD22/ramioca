import { motion } from "framer-motion"
import { Power, PowerOff, Wifi, WifiOff, FlaskConical } from "lucide-react"
import CompetitionHeader from "./CompetitionHeader"
import AgentPanel from "./AgentPanel"
import MarketScanner from "./MarketScanner"
import PolymarketTrades from "./PolymarketTrades"
import type { CompetitionState } from "../types"
import type { PnLHistory } from "../hooks/useWebSocket"

interface Props {
  state: CompetitionState
  connected: boolean
  onCommand: (cmd: string) => void
  pnlHistory: PnLHistory
}

function formatUptime(seconds: number): string {
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  const s = Math.floor(seconds % 60)
  return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`
}

export default function Dashboard({ state, connected, onCommand, pnlHistory }: Props) {
  const { alpha, bot_status, markets: _markets } = state

  const allSignals = alpha?.signals || []

  return (
    <div className="min-h-screen p-4 md:p-6 lg:p-8 max-w-[1400px] mx-auto">
      {/* Header */}
      <motion.header
        initial={{ opacity: 0, y: -20 }}
        animate={{ opacity: 1, y: 0 }}
        className="flex items-center justify-between mb-6"
      >
        <div className="flex items-center gap-4">
          <h1 className="text-2xl md:text-3xl font-bold tracking-tight">
            <span className="neon-text-cyan">RAMI</span>
            <span className="text-white/60">OCA</span>
          </h1>
          <div className="hidden md:flex items-center gap-2 glass-card px-3 py-1.5 rounded-full">
            {bot_status?.dry_run && (
              <span className="flex items-center gap-1.5 text-[10px] font-mono uppercase text-neon-yellow">
                <FlaskConical className="w-3 h-3" />
                Dry Run
              </span>
            )}
          </div>
        </div>

        <div className="flex items-center gap-3">
          {/* Connection status */}
          <div className="flex items-center gap-2 text-xs font-mono">
            {connected ? (
              <>
                <Wifi className="w-3.5 h-3.5 text-neon-green" />
                <span className="text-neon-green/60">LIVE</span>
              </>
            ) : (
              <>
                <WifiOff className="w-3.5 h-3.5 text-neon-red" />
                <span className="text-neon-red/60">OFFLINE</span>
              </>
            )}
          </div>

          {/* Uptime */}
          <div className="hidden md:block text-xs font-mono text-white/25">
            {formatUptime(bot_status?.uptime_seconds || 0)}
          </div>

          {/* Bot controls */}
          <button
            onClick={() => onCommand(bot_status?.running ? "stop" : "start")}
            className={`flex items-center gap-2 px-4 py-2 rounded-xl text-sm font-medium transition-all cursor-pointer ${
              bot_status?.running
                ? "bg-neon-red/10 text-neon-red border border-neon-red/20 hover:bg-neon-red/20"
                : "bg-neon-green/10 text-neon-green border border-neon-green/20 hover:bg-neon-green/20"
            }`}
          >
            {bot_status?.running ? (
              <>
                <PowerOff className="w-4 h-4" />
                Stop
              </>
            ) : (
              <>
                <Power className="w-4 h-4" />
                Start
              </>
            )}
          </button>
        </div>
      </motion.header>

      {/* Claude Agent Header */}
      {alpha && (
        <CompetitionHeader alpha={alpha} />
      )}

      {/* Single Agent Panel — full width */}
      {alpha && (
        <div className="mb-6">
          <AgentPanel agent={alpha} color="cyan" delay={0.1} pnlHistory={pnlHistory.alpha} />
        </div>
      )}

      {/* Real Polymarket Trades — full width */}
      {state.polymarket_trades && state.polymarket_trades.length > 0 && (
        <section className="mb-6">
          <PolymarketTrades trades={state.polymarket_trades} />
        </section>
      )}

      {/* Market Scanner — full width */}
      <section>
        <MarketScanner signals={allSignals} />
      </section>

      {/* Footer */}
      <motion.footer
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ delay: 0.6 }}
        className="mt-8 flex items-center justify-between text-[10px] font-mono text-white/15 px-2"
      >
        <span>RAMIOCA v3.0 | Claude Sonnet Agent</span>
        <span>{bot_status?.markets_tracked || 0} markets tracked</span>
        <span>
          {bot_status?.last_scan
            ? `Last scan: ${new Date(bot_status.last_scan).toLocaleTimeString()}`
            : "Waiting for first scan..."
          }
        </span>
      </motion.footer>
    </div>
  )
}
