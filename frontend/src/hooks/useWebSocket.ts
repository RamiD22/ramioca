import { useEffect, useRef, useState, useCallback } from "react"
import type { CompetitionState, PnLSnapshot } from "../types"

const EMPTY_METRICS = {
  balance: 0, total_pnl: 0, total_pnl_pct: 0, win_rate: 0,
  total_trades: 0, winning_trades: 0, losing_trades: 0,
  active_positions: 0, total_exposure: 0, sharpe_ratio: 0,
  max_drawdown: 0, avg_trade_pnl: 0,
}

const INITIAL_STATE: CompetitionState = {
  alpha: {
    agent_id: "alpha",
    label: "Alpha (v2)",
    metrics: { ...EMPTY_METRICS },
    positions: [],
    recent_trades: [],
    signals: [],
    activity_log: [],
  },
  beta: {
    agent_id: "beta",
    label: "Beta (v1)",
    metrics: { ...EMPTY_METRICS },
    positions: [],
    recent_trades: [],
    signals: [],
    activity_log: [],
  },
  bot_status: {
    running: false, dry_run: true, uptime_seconds: 0,
    last_scan: null, markets_tracked: 0, errors: [],
  },
  markets: [],
  polymarket_trades: [],
}

export interface PnLHistory {
  alpha: PnLSnapshot[]
  beta: PnLSnapshot[]
}

export function useWebSocket() {
  const [state, setState] = useState<CompetitionState>(INITIAL_STATE)
  const [connected, setConnected] = useState(false)
  const [pnlHistory, setPnlHistory] = useState<PnLHistory>({ alpha: [], beta: [] })
  const wsRef = useRef<WebSocket | null>(null)
  const reconnectTimer = useRef<ReturnType<typeof setTimeout>>(undefined)

  const connect = useCallback(() => {
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:"
    const wsUrl = `${protocol}//${window.location.host}/ws`

    const ws = new WebSocket(wsUrl)
    wsRef.current = ws

    ws.onopen = () => {
      setConnected(true)
      console.log("[WS] Connected")
    }

    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data)
        if (msg.type === "state_update" && msg.data) {
          setState(msg.data)
        }
      } catch {
        // ignore parse errors
      }
    }

    ws.onclose = () => {
      setConnected(false)
      console.log("[WS] Disconnected — reconnecting in 3s")
      reconnectTimer.current = setTimeout(connect, 3000)
    }

    ws.onerror = () => {
      ws.close()
    }
  }, [])

  // Fetch historical PnL from Supabase on mount
  useEffect(() => {
    async function fetchPnlHistory() {
      try {
        const [alphaRes, betaRes] = await Promise.all([
          fetch("/api/agent/alpha/pnl-history?limit=500"),
          fetch("/api/agent/beta/pnl-history?limit=500"),
        ])
        const [alphaData, betaData] = await Promise.all([
          alphaRes.json(),
          betaRes.json(),
        ])
        setPnlHistory({
          alpha: alphaData.snapshots || [],
          beta: betaData.snapshots || [],
        })
      } catch (err) {
        console.warn("[PnL] Failed to fetch history:", err)
      }
    }
    fetchPnlHistory()
    // Refresh every 5 minutes
    const interval = setInterval(fetchPnlHistory, 5 * 60 * 1000)
    return () => clearInterval(interval)
  }, [])

  useEffect(() => {
    connect()
    return () => {
      clearTimeout(reconnectTimer.current)
      wsRef.current?.close()
    }
  }, [connect])

  const sendCommand = useCallback((command: string) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ command }))
    }
  }, [])

  return { state, connected, sendCommand, pnlHistory }
}
