"""FastAPI app — REST + WebSocket endpoints for the dual-agent trading bot dashboard."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from zoneinfo import ZoneInfo

from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from backend.bot.agent import TradingAgent, StrategyState
from backend.bot.client import polymarket
from backend.bot.executor import Executor
from backend.bot.strategy import analyze_market as v2_strategy
from backend.bot.strategy_v1 import analyze_market_v1 as v1_strategy
from backend.bot.claude_strategy import analyze_market_claude, claude_strategy
from backend.config import settings
from backend.models import (
    ActivityEvent,
    AgentState,
    BotStatus,
    CompetitionState,
    DashboardState,
    MarketInfo,
    Timeframe,
)
from backend.services.db import persist_trade, persist_pnl_snapshot, fetch_pnl_history
from backend.services.market_scanner import fetch_live_5m_markets, fetch_crypto_markets
from backend.services.portfolio import compute_metrics_from_polymarket, fetch_raw_positions, fetch_raw_trades, sync_balance, sync_positions_for_agent, cleanup_settled_positions
from backend.services.price_feed import price_feed

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# ── Global state ──
AGENT_BUDGET = 500.0  # Full budget for single Claude agent

connected_clients: set[WebSocket] = set()
bot_start_time: float = 0
bot_running = False

# Single Claude agent — initialized in bot_loop()
claude_agent: TradingAgent | None = None

# Shared bot status
shared_bot_status = BotStatus()
shared_markets: list[MarketInfo] = []

# Cached real Polymarket trades (updated each bot cycle)
_polymarket_trades_cache: list[dict] = []

MAX_ACTIVITY_EVENTS = 100


def emit_event(
    agent: TradingAgent | None,
    event_type: str,
    title: str,
    detail: str = "",
    market: str = "",
    icon: str = "info",
    severity: str = "info",
) -> None:
    """Add an activity event to an agent's log (or shared if agent=None)."""
    event = ActivityEvent(
        id=str(uuid.uuid4())[:8],
        timestamp=datetime.now(timezone.utc),
        event_type=event_type,
        title=title,
        detail=detail,
        market=market,
        icon=icon,
        severity=severity,
        agent=agent.agent_id if agent else "",
    )
    if agent:
        agent.state.activity_log.insert(0, event)
        agent.state.activity_log = agent.state.activity_log[:MAX_ACTIVITY_EVENTS]
    else:
        if claude_agent:
            claude_agent.state.activity_log.insert(0, event)
            claude_agent.state.activity_log = claude_agent.state.activity_log[:MAX_ACTIVITY_EVENTS]


async def broadcast(data: dict) -> None:
    """Send state update to all connected dashboard clients."""
    dead: set[WebSocket] = set()
    msg = json.dumps(data, default=str)
    for ws in connected_clients:
        try:
            await ws.send_text(msg)
        except Exception:
            dead.add(ws)
    connected_clients.difference_update(dead)


def _get_polymarket_trades() -> list[dict]:
    """Return cached Polymarket trades grouped by market with PnL."""
    return _compute_market_pnl(_polymarket_trades_cache)


def _compute_market_pnl(trades: list[dict]) -> list[dict]:
    """Group trades by conditionId+outcome, compute per-market PnL."""
    from collections import defaultdict

    groups: dict[str, list[dict]] = defaultdict(list)
    for t in trades:
        key = f"{t.get('conditionId', '')}_{t.get('outcomeIndex', 0)}"
        groups[key].append(t)

    positions = []
    for _key, group_trades in groups.items():
        group_trades.sort(key=lambda x: x.get("timestamp", 0))

        total_bought_shares = 0.0
        total_bought_cost = 0.0
        total_sold_shares = 0.0
        total_sold_revenue = 0.0

        enriched_trades = []
        for t in group_trades:
            shares = float(t.get("size", 0))
            price = float(t.get("price", 0))
            usdc = round(shares * price, 2)
            enriched_trades.append({
                "side": t.get("side", ""),
                "shares": round(shares, 2),
                "price": round(price, 4),
                "usdc": usdc,
                "timestamp": t.get("timestamp", 0),
                "transactionHash": t.get("transactionHash", ""),
            })
            if t.get("side") == "BUY":
                total_bought_shares += shares
                total_bought_cost += usdc
            else:
                total_sold_shares += shares
                total_sold_revenue += usdc

        net_shares = total_bought_shares - total_sold_shares
        realized_pnl = total_sold_revenue - total_bought_cost
        avg_entry = total_bought_cost / total_bought_shares if total_bought_shares > 0 else 0

        latest = group_trades[-1]
        positions.append({
            "title": latest.get("title", ""),
            "outcome": latest.get("outcome", ""),
            "slug": latest.get("slug", ""),
            "total_cost": round(total_bought_cost, 2),
            "total_revenue": round(total_sold_revenue, 2),
            "net_shares": round(net_shares, 2),
            "avg_entry": round(avg_entry, 4),
            "pnl": round(realized_pnl, 2),
            "status": "closed" if net_shares < 0.5 else "open",
            "trade_count": len(group_trades),
            "last_trade_time": latest.get("timestamp", 0),
            "trades": enriched_trades,
        })

    positions.sort(key=lambda x: x["last_trade_time"], reverse=True)
    return positions


def build_competition_state() -> CompetitionState:
    """Build the dashboard state — Claude agent in alpha slot, beta empty."""
    def _agent_state(agent: TradingAgent) -> AgentState:
        return AgentState(
            agent_id=agent.agent_id,
            label=agent.label,
            metrics=agent.state.metrics,
            positions=agent.state.positions,
            recent_trades=agent.state.recent_trades,
            signals=agent.state.signals,
            activity_log=agent.state.activity_log,
        )

    return CompetitionState(
        alpha=_agent_state(claude_agent) if claude_agent else AgentState(agent_id="alpha", label="Claude (Opus)"),
        beta=AgentState(agent_id="beta", label="(inactive)"),
        bot_status=shared_bot_status,
        markets=shared_markets,
        polymarket_trades=_get_polymarket_trades(),
    )




async def run_agent_cycle(
    agent: TradingAgent,
    markets: list[MarketInfo],
    raw_positions: list[dict],
    raw_trades: list[dict],
) -> None:
    """Run one cycle for a single agent: analyze → execute → update."""
    # Sync only positions that Claude owns (filtered by owned_token_ids)
    sync_positions_for_agent(agent.state, agent.owned_token_ids, raw_positions, agent.owned_sizes)

    # Clean up settled/expired positions from ownership tracking
    settled_tokens = cleanup_settled_positions(raw_positions, agent.owned_token_ids)
    if settled_tokens:
        agent.owned_token_ids -= settled_tokens
        for tid in settled_tokens:
            agent.owned_sizes.pop(tid, None)
        logger.info(f"[{agent.agent_id}] Cleaned up {len(settled_tokens)} settled positions")

    # Run strategy on each live 5-minute market
    # Markets arrive pre-filtered from fetch_live_5m_markets() — symbol in market.category
    signals = []

    # Compute window elapsed percentage (how far into the current 5-min window)
    now_et = datetime.now(_ET)
    elapsed_seconds = (now_et.minute % 5) * 60 + now_et.second
    window_elapsed_pct = elapsed_seconds / 300.0  # 300s = 5 minutes

    for market in markets:
        symbol = market.category  # Set by scanner: "BTCUSDT", "ETHUSDT", etc.

        price_data = price_feed.get_prices(symbol)
        if not any(price_data.values()):
            logger.warning(f"[{agent.agent_id}] SKIP (no price data): {symbol}")
            continue

        # Enrich market with window context for strategy use
        market.window_delta = price_feed.get_window_delta(symbol)
        market.window_elapsed_pct = window_elapsed_pct

        logger.info(
            f"[{agent.agent_id}] Analyzing: {market.question[:50]} ({symbol}) "
            f"Δ={market.window_delta:+.4f} elapsed={window_elapsed_pct:.0%}"
        )
        signal = agent.strategy_fn(market, price_data, agent.strategy_state)
        signals.append(signal)

        mkt_short = market.question[:55]

        # Check if market price is in our tradeable range
        mp = signal.market_price
        in_range = settings.MIN_PRICE <= mp <= settings.MAX_PRICE

        # Execute if there's an edge
        if signal.recommended_side is not None:
            logger.info(
                f"[{agent.agent_id}] SIGNAL: {signal.composite_signal} {signal.recommended_side} "
                f"edge={signal.edge:.3f} on {market.question[:60]}"
            )

            trade = agent.executor.execute_signal(signal)

            if trade:
                agent.owned_token_ids.add(signal.token_id)
                # Track how much this agent invested in this token
                agent.owned_sizes[signal.token_id] = agent.owned_sizes.get(signal.token_id, 0) + trade.size
                persist_trade(trade, agent.agent_id)
                emit_event(
                    agent,
                    "trade",
                    f"{'🟢' if signal.recommended_side.value == 'BUY' else '🔴'} {signal.recommended_side.value} ${trade.size:.2f}",
                    f"Edge: {signal.edge:.3f} | {signal.composite_signal.value} | Price: {trade.price:.4f}",
                    market=mkt_short,
                    icon=f"trade_{signal.recommended_side.value.lower()}",
                    severity="success",
                )
            elif in_range:
                emit_event(
                    agent,
                    "skip",
                    f"Signal blocked by risk",
                    f"{signal.composite_signal.value} {signal.recommended_side.value} edge={signal.edge:.3f} — filtered by risk checks",
                    market=mkt_short,
                    icon="skip",
                    severity="warning",
                )
        elif in_range:
            if abs(signal.edge) > 0.005:
                emit_event(
                    agent,
                    "signal",
                    f"Analyzing — {signal.composite_signal.value}",
                    f"Edge: {signal.edge:.3f} | Prob: {signal.probability_estimate:.1%} vs Market: {signal.market_price:.1%} — below threshold",
                    market=mkt_short,
                    icon="signal",
                    severity="info",
                )

    agent.state.signals = signals

    # Check stop losses
    stopped = agent.executor.check_stop_losses()
    for cid in stopped:
        emit_event(agent, "stop_loss", "Position closed", f"Condition {cid[:12]}...", icon="stop", severity="warning")

    # Update metrics from REAL Polymarket data (only Claude's positions + trades)
    owned_positions = [p for p in raw_positions if p.get("asset", "") in agent.owned_token_ids]
    owned_trades = [t for t in raw_trades if t.get("asset", "") in agent.owned_token_ids]
    metrics = compute_metrics_from_polymarket(owned_positions, owned_trades, balance=agent.budget)
    agent.state.metrics = metrics
    persist_pnl_snapshot(agent.agent_id, metrics)


async def bot_loop() -> None:
    """Main trading bot loop — single Claude agent."""
    global bot_running, claude_agent, shared_bot_status, shared_markets
    bot_running = True

    logger.info("Bot loop started (dry_run=%s, Claude Opus agent)", settings.DRY_RUN)

    # ── Create Claude agent ──
    claude_agent = TradingAgent(
        agent_id="alpha",
        label="Claude (Opus)",
        strategy_fn=analyze_market_claude,
        budget=AGENT_BUDGET,
    )

    # Start fresh — no old trade history loading.
    # Metrics come from real Polymarket API positions, not internal records.

    # Initialize executor
    claude_agent.executor = Executor(
        claude_agent.state,
        strategy_state=claude_agent.strategy_state,
        max_exposure=AGENT_BUDGET,
    )

    emit_event(
        None, "info", "Claude (Opus) agent started",
        f"Budget: ${AGENT_BUDGET:.0f} | DRY_RUN={'ON' if settings.DRY_RUN else 'OFF'}",
        icon="info", severity="info",
    )

    # Initialize Polymarket client
    try:
        polymarket.initialize()
        emit_event(None, "info", "CLOB client initialized", f"Chain {settings.CHAIN_ID} | Sig type {settings.SIGNATURE_TYPE}", icon="info", severity="success")
    except Exception as e:
        logger.error(f"Client init failed: {e}")
        shared_bot_status.errors.append(str(e))
        emit_event(None, "error", "Client init failed", str(e), icon="error", severity="error")

    # Load historical price data
    await price_feed.load_all_historical()
    emit_event(None, "info", "Price feeds loaded", "BTC, ETH, SOL, XRP, DOGE — 5m/1h/4h candles", icon="info", severity="success")

    while bot_running:
        try:
            cycle_start = time.time()

            # ── Update shared status early so dashboard always shows live ──
            shared_bot_status.running = True
            shared_bot_status.dry_run = settings.DRY_RUN
            shared_bot_status.uptime_seconds = time.time() - bot_start_time

            # ── 1. Scan live 5-minute markets (shared) ──
            live_5m = await fetch_live_5m_markets()
            shared_markets = live_5m  # Dashboard shows live markets
            shared_bot_status.markets_tracked = len(live_5m)
            shared_bot_status.last_scan = datetime.now(timezone.utc)

            if live_5m:
                names = ", ".join(m.question[:30] for m in live_5m[:4])
                emit_event(None, "scan", f"{len(live_5m)} live 5M markets", names, icon="scan", severity="info")
            else:
                emit_event(None, "scan", "No live 5M markets right now", "Waiting for next 5-minute window...", icon="scan", severity="info")

            # ── 2. Fetch real Polymarket data (positions + trades) ──
            global _polymarket_trades_cache
            raw_positions = fetch_raw_positions()
            raw_trades = fetch_raw_trades()
            # Cache ALL wallet trades (PnL is computed per-market on read)
            _polymarket_trades_cache = raw_trades

            # ── 3. Run Claude agent ──
            try:
                await run_agent_cycle(claude_agent, live_5m, raw_positions, raw_trades)
            except Exception as agent_err:
                logger.error(f"[claude] cycle error: {agent_err}", exc_info=True)
                emit_event(claude_agent, "error", "Cycle error", str(agent_err)[:100], icon="error", severity="error")

            # ── 4. Broadcast competition state (always runs) ──
            comp_state = build_competition_state()
            await broadcast({"type": "state_update", "data": comp_state.model_dump()})

            # Wait for next cycle
            elapsed = time.time() - cycle_start
            sleep_time = max(settings.POLL_INTERVAL - elapsed, 1)
            await asyncio.sleep(sleep_time)

        except Exception as e:
            logger.error(f"Bot loop error: {e}", exc_info=True)
            shared_bot_status.errors.append(str(e))
            shared_bot_status.errors = shared_bot_status.errors[-20:]
            emit_event(None, "error", "Bot loop error", str(e)[:100], icon="error", severity="error")
            await asyncio.sleep(5)


def _market_to_symbol(question: str) -> str | None:
    """Map a market question to a Binance symbol."""
    q = question.lower()
    if "bitcoin" in q or "btc" in q:
        return "BTCUSDT"
    if "ethereum" in q or "eth " in q or "eth?" in q or q.endswith("eth"):
        return "ETHUSDT"
    if "solana" in q or "sol " in q or "sol?" in q or q.endswith("sol"):
        return "SOLUSDT"
    if "xrp" in q:
        return "XRPUSDT"
    if "dogecoin" in q or "doge" in q:
        return "DOGEUSDT"
    return None


_ET = ZoneInfo("America/New_York")

# Format 1: "March 12, 8:40AM-8:45AM ET" (time range — captures start AND end)
_RANGE_TIME_RE = re.compile(
    r"(\w+)\s+(\d{1,2}),\s*(\d{1,2}):(\d{2})([AP]M)\s*-\s*(\d{1,2}):(\d{2})([AP]M)\s*ET",
    re.IGNORECASE,
)
# Format 2: "March 13, 6AM ET" (single time, no range)
_SINGLE_TIME_RE = re.compile(
    r"(\w+)\s+(\d{1,2}),\s*(\d{1,2})(?::(\d{2}))?([AP]M)\s*ET",
    re.IGNORECASE,
)
_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
}


def _parse_ampm(hour: int, ampm: str) -> int:
    """Convert 12-hour format to 24-hour."""
    if ampm.upper() == "PM" and hour != 12:
        return hour + 12
    elif ampm.upper() == "AM" and hour == 12:
        return 0
    return hour


def _is_market_live(question: str) -> bool:
    """Check if we're currently inside a 5-min market's trading window.

    Parses start AND end time from questions like:
      'Bitcoin Up or Down - March 12, 8:40AM-8:45AM ET'  (range format)
      'Solana Up or Down - March 13, 6AM ET'              (single time: assume 5-min window)
    Returns True only if start_time <= now < end_time (currently tradeable).
    """
    now_et = datetime.now(_ET)

    # Try range format first (preferred — gives us start AND end time)
    m = _RANGE_TIME_RE.search(question)
    if m:
        month_str, day_str, start_h, start_m, start_ampm, end_h, end_m, end_ampm = m.groups()
        month = _MONTHS.get(month_str.lower())
        if not month:
            return False

        s_hour = _parse_ampm(int(start_h), start_ampm)
        s_min = int(start_m)
        e_hour = _parse_ampm(int(end_h), end_ampm)
        e_min = int(end_m)

        try:
            start_dt = now_et.replace(month=month, day=int(day_str), hour=s_hour, minute=s_min, second=0, microsecond=0)
            end_dt = now_et.replace(month=month, day=int(day_str), hour=e_hour, minute=e_min, second=0, microsecond=0)
        except ValueError:
            return False

        return start_dt <= now_et < end_dt

    # Try single time format (e.g., "6AM ET" → assume 5-min window ending at that time)
    m = _SINGLE_TIME_RE.search(question)
    if m:
        month_str, day_str, end_h, end_m_str, ampm = m.groups()
        month = _MONTHS.get(month_str.lower())
        if not month:
            return False

        e_hour = _parse_ampm(int(end_h), ampm)
        e_min = int(end_m_str) if end_m_str else 0

        try:
            end_dt = now_et.replace(month=month, day=int(day_str), hour=e_hour, minute=e_min, second=0, microsecond=0)
        except ValueError:
            return False

        # Assume 5-minute window
        from datetime import timedelta
        start_dt = end_dt - timedelta(minutes=5)
        return start_dt <= now_et < end_dt

    return False  # can't parse → skip


@asynccontextmanager
async def lifespan(app: FastAPI):
    global bot_start_time
    bot_start_time = time.time()

    # Start price feed websocket
    ws_task = asyncio.create_task(price_feed.start_ws())
    # Start bot loop
    bot_task = asyncio.create_task(bot_loop())

    yield

    # Cleanup
    global bot_running
    bot_running = False
    price_feed.stop()
    ws_task.cancel()
    bot_task.cancel()


app = FastAPI(title="Ramioca Trading Bot", version="2.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- REST Endpoints ---


@app.get("/api/state")
async def get_state():
    return build_competition_state().model_dump()


@app.get("/api/markets")
async def get_markets():
    return [m.model_dump() for m in shared_markets]


@app.get("/api/agent/{agent_id}/positions")
async def get_agent_positions(agent_id: str):
    if not claude_agent:
        return []
    return [p.model_dump() for p in claude_agent.state.positions]


@app.get("/api/agent/{agent_id}/metrics")
async def get_agent_metrics(agent_id: str):
    if not claude_agent:
        return {}
    return claude_agent.state.metrics.model_dump()


@app.get("/api/status")
async def get_bot_status():
    return shared_bot_status.model_dump()


@app.get("/api/agent/{agent_id}/pnl-history")
async def get_pnl_history(agent_id: str, limit: int = 500):
    """Fetch historical PnL snapshots from Supabase for charting."""
    if agent_id not in ("alpha", "beta"):
        return {"error": "Invalid agent_id"}
    data = fetch_pnl_history(agent_id, limit=limit)
    return {"agent": agent_id, "snapshots": data}


@app.get("/api/claude/stats")
async def get_claude_stats():
    """Return Claude agent API usage stats."""
    return claude_strategy.stats


@app.get("/api/polymarket/trades")
async def get_polymarket_trades():
    """Return cached real Polymarket trades."""
    return {"trades": _get_polymarket_trades()}


@app.post("/api/bot/start")
async def start_bot():
    global bot_running
    if not bot_running:
        bot_running = True
        asyncio.create_task(bot_loop())
    return {"status": "started"}


@app.post("/api/bot/stop")
async def stop_bot():
    global bot_running
    bot_running = False
    shared_bot_status.running = False
    comp_state = build_competition_state()
    await broadcast({"type": "state_update", "data": comp_state.model_dump()})
    return {"status": "stopped"}


# --- WebSocket ---


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    connected_clients.add(ws)
    logger.info(f"Dashboard client connected ({len(connected_clients)} total)")

    # Send current state immediately
    try:
        comp_state = build_competition_state()
        await ws.send_text(json.dumps({"type": "state_update", "data": comp_state.model_dump()}, default=str))
    except Exception:
        pass

    try:
        while True:
            msg = await ws.receive_text()
            data = json.loads(msg)
            cmd = data.get("command")

            if cmd == "start":
                await start_bot()
            elif cmd == "stop":
                await stop_bot()
            elif cmd == "refresh":
                comp_state = build_competition_state()
                await ws.send_text(
                    json.dumps({"type": "state_update", "data": comp_state.model_dump()}, default=str)
                )
    except WebSocketDisconnect:
        pass
    finally:
        connected_clients.discard(ws)
        logger.info(f"Dashboard client disconnected ({len(connected_clients)} total)")


# --- Static frontend serving (production) ---

_FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend" / "dist"

if _FRONTEND_DIR.is_dir():
    _assets_dir = _FRONTEND_DIR / "assets"
    if _assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=_assets_dir), name="static-assets")

    @app.get("/{full_path:path}")
    async def spa_catch_all(full_path: str):
        """Serve static file if it exists, otherwise index.html for SPA routing."""
        file_path = _FRONTEND_DIR / full_path
        if full_path and file_path.is_file():
            return FileResponse(file_path)
        return FileResponse(_FRONTEND_DIR / "index.html")
