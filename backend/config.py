import os
from pathlib import Path
from dotenv import load_dotenv

_env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(_env_path, override=True)


class Settings:
    # Polymarket auth — maps to your existing .env vars
    PRIVATE_KEY: str = os.getenv("POLYMARKET_PRIVATE_KEY", "")
    API_KEY: str = os.getenv("POLYMARKET_CLOB_API_KEY", "")
    API_SECRET: str = os.getenv("POLYMARKET_CLOB_SECRET", "")
    API_PASSPHRASE: str = os.getenv("POLYMARKET_CLOB_PASSPHRASE", "")
    CHAIN_ID: int = 137
    CLOB_HOST: str = "https://clob.polymarket.com"
    GAMMA_HOST: str = "https://gamma-api.polymarket.com"
    DATA_HOST: str = "https://data-api.polymarket.com"
    FUNDER: str = os.getenv("POLYMARKET_FUNDER_ADDRESS", "")
    SIGNATURE_TYPE: int = int(os.getenv("POLYMARKET_SIGNATURE_TYPE", "1"))

    # Risk parameters — pulled from your existing config
    MAX_POSITION_SIZE: float = float(os.getenv("POLYMARKET_AGENT_MAX_MARKET_USDC", "10"))
    MAX_TOTAL_EXPOSURE: float = float(os.getenv("POLYMARKET_AGENT_BUDGET_USDC", "200"))
    STOP_LOSS_PCT: float = float(os.getenv("POLYMARKET_AGENT_STOP_LOSS_PCT", "8")) / 100
    TAKE_PROFIT_PCT: float = float(os.getenv("POLYMARKET_AGENT_TAKE_PROFIT_PCT", "12")) / 100
    MAX_POSITIONS: int = int(os.getenv("POLYMARKET_AGENT_MAX_POSITIONS", "4"))
    CLIP_SIZE: float = float(os.getenv("POLYMARKET_AGENT_CLIP_USDC", "10"))
    DAILY_LOSS_LIMIT: float = float(os.getenv("POLYMARKET_AGENT_DAILY_LOSS_USDC", "15"))

    # Signal thresholds
    MIN_SIGNAL: float = float(os.getenv("POLYMARKET_AGENT_MIN_SIGNAL", "0.56"))
    MIN_PRICE: float = float(os.getenv("POLYMARKET_AGENT_MIN_PRICE", "0.15"))
    MAX_PRICE: float = float(os.getenv("POLYMARKET_AGENT_MAX_PRICE", "0.85"))
    MIN_CONFIDENCE: float = float(os.getenv("POLYMARKET_AGENT_MIN_CONFIDENCE", "0.70"))
    MIN_LIQUIDITY: float = float(os.getenv("POLYMARKET_AGENT_MIN_LIQUIDITY_USDC", "1500"))
    MIN_VOLUME_24H: float = float(os.getenv("POLYMARKET_AGENT_MIN_VOLUME24H_USDC", "2500"))

    # Bot settings
    POLL_INTERVAL: int = int(os.getenv("POLYMARKET_AGENT_INTERVAL_SECONDS", "2"))
    DRY_RUN: bool = os.getenv("DRY_RUN", "true").lower() == "true"
    AUTOTRADE: bool = os.getenv("POLYMARKET_AGENT_AUTOTRADE", "true").lower() == "true"

    # Anthropic (for Claude-powered trading agent)
    ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
    ANTHROPIC_MODEL: str = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-20250514")

    # Supabase (optional — bot works without it)
    SUPABASE_URL: str = os.getenv("SUPABASE_URL", "")
    SUPABASE_KEY: str = os.getenv("SUPABASE_KEY", "")


settings = Settings()
