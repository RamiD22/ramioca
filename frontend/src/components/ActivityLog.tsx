import { motion, AnimatePresence } from "framer-motion"
import {
  Search, TrendingUp, ArrowUpRight, ArrowDownRight,
  ShieldAlert, XCircle, Info, Zap, Ban
} from "lucide-react"
import type { ActivityEvent } from "../types"

interface Props {
  events: ActivityEvent[]
  compact?: boolean
}

function timeAgo(ts: string): string {
  const diff = Date.now() - new Date(ts).getTime()
  const secs = Math.floor(diff / 1000)
  if (secs < 5) return "now"
  if (secs < 60) return `${secs}s ago`
  const mins = Math.floor(secs / 60)
  if (mins < 60) return `${mins}m ago`
  const hours = Math.floor(mins / 60)
  return `${hours}h ago`
}

const ICON_MAP: Record<string, React.ReactNode> = {
  scan: <Search className="w-3.5 h-3.5" />,
  signal: <TrendingUp className="w-3.5 h-3.5" />,
  trade_buy: <ArrowUpRight className="w-3.5 h-3.5" />,
  trade_sell: <ArrowDownRight className="w-3.5 h-3.5" />,
  skip: <Ban className="w-3.5 h-3.5" />,
  stop: <ShieldAlert className="w-3.5 h-3.5" />,
  profit: <Zap className="w-3.5 h-3.5" />,
  error: <XCircle className="w-3.5 h-3.5" />,
  info: <Info className="w-3.5 h-3.5" />,
}

const SEVERITY_STYLES: Record<string, { bg: string; text: string; border: string; accent: string }> = {
  info: { bg: "bg-neon-cyan/5", text: "text-neon-cyan/70", border: "border-neon-cyan/10", accent: "border-l-neon-cyan/40" },
  success: { bg: "bg-neon-green/8", text: "text-neon-green", border: "border-neon-green/15", accent: "border-l-neon-green/60" },
  warning: { bg: "bg-neon-yellow/8", text: "text-neon-yellow", border: "border-neon-yellow/15", accent: "border-l-neon-yellow/60" },
  error: { bg: "bg-neon-red/8", text: "text-neon-red", border: "border-neon-red/15", accent: "border-l-neon-red/60" },
}

const TYPE_BADGE: Record<string, { label: string; cls: string }> = {
  scan: { label: "SCAN", cls: "bg-neon-cyan/10 text-neon-cyan/60" },
  signal: { label: "SIGNAL", cls: "bg-neon-magenta/10 text-neon-magenta/80" },
  trade: { label: "TRADE", cls: "bg-neon-green/15 text-neon-green" },
  skip: { label: "SKIP", cls: "bg-neon-yellow/10 text-neon-yellow/70" },
  stop_loss: { label: "STOP", cls: "bg-neon-red/15 text-neon-red" },
  take_profit: { label: "PROFIT", cls: "bg-neon-green/15 text-neon-green" },
  error: { label: "ERROR", cls: "bg-neon-red/10 text-neon-red" },
  info: { label: "INFO", cls: "bg-white/5 text-white/40" },
}

export default function ActivityLog({ events, compact = false }: Props) {
  const maxHeight = compact ? "max-h-[280px]" : "max-h-[450px]"
  const maxEvents = compact ? 40 : 80

  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay: 0.3, duration: 0.5 }}
      className={`glass-card ${compact ? "p-3" : "p-5"} h-full`}
    >
      <div className="flex items-center justify-between mb-3">
        <h2 className={`${compact ? "text-xs" : "text-sm"} font-medium tracking-widest uppercase text-white/40`}>
          Activity Log
        </h2>
        <span className="text-[10px] font-mono text-white/20">
          {events.length} events
        </span>
      </div>

      <div className={`space-y-0.5 ${maxHeight} overflow-y-auto pr-1 scrollbar-thin scroll-smooth`} style={{ overscrollBehavior: "contain" }}>
        <AnimatePresence initial={false}>
          {events.length === 0 ? (
            <div className="text-white/20 text-xs font-mono text-center py-6">
              Waiting for activity...
            </div>
          ) : (
            events.slice(0, maxEvents).map((evt) => {
              const sev = SEVERITY_STYLES[evt.severity] || SEVERITY_STYLES.info
              const badge = TYPE_BADGE[evt.event_type] || TYPE_BADGE.info
              const icon = ICON_MAP[evt.icon] || ICON_MAP.info

              return (
                <motion.div
                  key={evt.id}
                  initial={{ opacity: 0, x: -12, height: 0 }}
                  animate={{ opacity: 1, x: 0, height: "auto" }}
                  exit={{ opacity: 0, height: 0 }}
                  transition={{ duration: 0.2 }}
                  className={`flex items-start gap-2 py-1.5 px-2 rounded-lg border border-l-2 ${sev.bg} ${sev.border} ${sev.accent} hover:bg-white/[0.03] transition-colors`}
                >
                  {/* Icon */}
                  <div className={`flex-shrink-0 mt-0.5 ${sev.text}`}>
                    {icon}
                  </div>

                  {/* Content */}
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-1.5">
                      <span className={`text-[8px] font-mono font-bold uppercase px-1 py-0.5 rounded ${badge.cls}`}>
                        {badge.label}
                      </span>
                      <span className="text-[9px] font-mono text-white/20">
                        {timeAgo(evt.timestamp)}
                      </span>
                    </div>
                    <div className="text-[11px] text-white/80 mt-0.5 leading-snug">
                      {evt.title}
                    </div>
                    {evt.market && !compact && (
                      <div className="text-[9px] text-white/30 font-mono truncate mt-0.5">
                        {evt.market}
                      </div>
                    )}
                    {evt.detail && !compact && (
                      <div className="text-[9px] text-white/25 font-mono mt-0.5 leading-relaxed">
                        {evt.detail}
                      </div>
                    )}
                  </div>
                </motion.div>
              )
            })
          )}
        </AnimatePresence>
      </div>
    </motion.div>
  )
}
