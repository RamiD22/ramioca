import { useMemo } from "react"
import {
  AreaChart, Area, XAxis, YAxis, Tooltip,
  ResponsiveContainer, CartesianGrid, ReferenceLine,
} from "recharts"
import { motion } from "framer-motion"
import type { TradeRecord, PnLSnapshot } from "../types"

interface Props {
  trades: TradeRecord[]
  history?: PnLSnapshot[]
  gradientId?: string
  strokeColor?: string
}

/* Custom dot: only renders on the last data point with animated glow */
function LastPointDot({ cx, cy, index, total, color }: any) {
  if (index !== total - 1) return null
  return (
    <g>
      <circle cx={cx} cy={cy} r={8} fill="none" stroke={color} strokeWidth={1} opacity={0.2}>
        <animate attributeName="r" values="6;10;6" dur="2s" repeatCount="indefinite" />
        <animate attributeName="opacity" values="0.3;0.1;0.3" dur="2s" repeatCount="indefinite" />
      </circle>
      <circle
        cx={cx} cy={cy} r={4}
        fill={color} stroke="#0a0a0f" strokeWidth={2}
        style={{ filter: `drop-shadow(0 0 6px ${color})` }}
      />
    </g>
  )
}

export default function PnLChart({ trades, history, gradientId = "pnlGradient", strokeColor }: Props) {
  const data = useMemo(() => {
    // If we have historical snapshots from Supabase, use them as the primary source
    if (history && history.length > 0) {
      const historyPoints = history.map((s) => {
        const d = new Date(s.created_at)
        return {
          time: `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`,
          pnl: Number(s.total_pnl.toFixed(2)),
          ts: d.getTime(),
        }
      })

      // Deduplicate by minute (keep the last snapshot per minute)
      const byMinute = new Map<string, typeof historyPoints[0]>()
      for (const pt of historyPoints) {
        byMinute.set(pt.time, pt)
      }
      const deduped = Array.from(byMinute.values()).sort((a, b) => a.ts - b.ts)

      // If we also have live trades newer than last snapshot, append them
      if (trades.length > 0) {
        const lastSnapshotTs = deduped[deduped.length - 1]?.ts ?? 0
        const lastSnapshotPnl = deduped[deduped.length - 1]?.pnl ?? 0
        let cumulative = lastSnapshotPnl
        const liveTrades = [...trades].reverse().filter((t) => {
          const tTs = new Date(t.timestamp).getTime()
          return tTs > lastSnapshotTs
        })
        for (const t of liveTrades) {
          cumulative += t.pnl ?? 0
          const d = new Date(t.timestamp)
          const timeKey = `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`
          if (!byMinute.has(timeKey)) {
            deduped.push({
              time: timeKey,
              pnl: Number(cumulative.toFixed(2)),
              ts: d.getTime(),
            })
          }
        }
      }

      return deduped.map(({ time, pnl }) => ({ time, pnl }))
    }

    // Fallback: cumulative from live trades only (original behavior)
    if (trades.length === 0) {
      return Array.from({ length: 24 }, (_, i) => ({
        time: `${String(i).padStart(2, "0")}:00`,
        pnl: 0,
      }))
    }

    let cumulative = 0
    return [...trades].reverse().map((t) => {
      cumulative += t.pnl ?? 0
      const d = new Date(t.timestamp)
      return {
        time: `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`,
        pnl: Number(cumulative.toFixed(2)),
      }
    })
  }, [trades, history])

  const lastPnl = data[data.length - 1]?.pnl ?? 0
  const isPositive = lastPnl >= 0
  const color = strokeColor || (isPositive ? "#39ff14" : "#ff3131")

  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay: 0.2, duration: 0.5 }}
      className={isPositive ? "glass-card-glow-green" : "glass-card-glow-magenta"}
    >
      <div className="p-4 pb-0">
        <div className="flex items-center justify-between mb-1">
          <h2 className="text-xs font-medium tracking-widest uppercase text-white/40">
            Cumulative PnL
          </h2>
          <motion.span
            key={lastPnl.toFixed(2)}
            initial={{ opacity: 0.5, scale: 0.95 }}
            animate={{ opacity: 1, scale: 1 }}
            transition={{ duration: 0.3 }}
            className={`text-lg font-bold font-mono ${isPositive ? "neon-text-green" : "neon-text-red"}`}
          >
            {isPositive ? "+" : ""}${lastPnl.toFixed(2)}
          </motion.span>
        </div>
      </div>
      <div className="h-[260px] px-2 pb-3">
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={data} margin={{ top: 10, right: 10, left: 0, bottom: 0 }}>
            <defs>
              <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={color} stopOpacity={0.4} />
                <stop offset="100%" stopColor={color} stopOpacity={0} />
              </linearGradient>
            </defs>
            <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" />
            <ReferenceLine y={0} stroke="rgba(255,255,255,0.12)" strokeDasharray="4 4" />
            <XAxis
              dataKey="time"
              axisLine={false}
              tickLine={false}
              tick={{ fill: "rgba(255,255,255,0.25)", fontSize: 10, fontFamily: "monospace" }}
            />
            <YAxis
              axisLine={false}
              tickLine={false}
              tick={{ fill: "rgba(255,255,255,0.25)", fontSize: 10, fontFamily: "monospace" }}
              tickFormatter={(v) => `$${v}`}
              width={45}
            />
            <Tooltip
              contentStyle={{
                background: "rgba(18,18,26,0.95)",
                border: "1px solid rgba(255,255,255,0.1)",
                borderRadius: "12px",
                color: "#fff",
                fontFamily: "monospace",
                fontSize: 11,
              }}
              formatter={(value) => [`$${Number(value).toFixed(2)}`, "PnL"]}
            />
            <Area
              type="monotone"
              dataKey="pnl"
              stroke={color}
              strokeWidth={2.5}
              fill={`url(#${gradientId})`}
              dot={(props: any) => (
                <LastPointDot {...props} total={data.length} color={color} />
              )}
              activeDot={{ r: 5, fill: color, stroke: "#0a0a0f", strokeWidth: 2 }}
              style={{
                filter: `drop-shadow(0 0 8px ${color}66)`,
              }}
            />
          </AreaChart>
        </ResponsiveContainer>
      </div>
    </motion.div>
  )
}
