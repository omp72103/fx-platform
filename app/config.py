"""
Central configuration, loaded from environment variables (.env).
Nothing here should ever contain a hard-coded secret (section 25).
"""
import os
from dataclasses import dataclass, field
from dotenv import load_dotenv

load_dotenv()


def _list_env(name: str, default: str) -> list[str]:
    return [x.strip() for x in os.getenv(name, default).split(",") if x.strip()]


@dataclass(frozen=True)
class Settings:
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///./fx_platform.db")

    gateway_mode: str = os.getenv("GATEWAY_MODE", "mock")  # mock | mt5

    mt5_login: str = os.getenv("MT5_LOGIN", "")
    mt5_password: str = os.getenv("MT5_PASSWORD", "")
    mt5_server: str = os.getenv("MT5_SERVER", "")
    mt5_terminal_path: str = os.getenv("MT5_TERMINAL_PATH", "")

    # Hard safety guard. See app/execution_engine.py — orders are refused
    # unless this is "demo", regardless of any other setting, until a human
    # deliberately changes it AND the strategy has cleared validation.
    account_mode: str = os.getenv("ACCOUNT_MODE", "demo")

    risk_per_trade_pct: float = float(os.getenv("RISK_PER_TRADE_PCT", "1.0"))
    max_daily_loss_pct: float = float(os.getenv("MAX_DAILY_LOSS_PCT", "3.0"))
    max_weekly_loss_pct: float = float(os.getenv("MAX_WEEKLY_LOSS_PCT", "6.0"))
    max_drawdown_pct: float = float(os.getenv("MAX_DRAWDOWN_PCT", "25.0"))
    max_open_positions: int = int(os.getenv("MAX_OPEN_POSITIONS", "5"))
    max_symbol_exposure_pct: float = float(os.getenv("MAX_SYMBOL_EXPOSURE_PCT", "10.0"))
    max_portfolio_exposure_pct: float = float(os.getenv("MAX_PORTFOLIO_EXPOSURE_PCT", "25.0"))
    max_spread_pips: float = float(os.getenv("MAX_SPREAD_PIPS", "3.0"))
    max_slippage_pips: float = float(os.getenv("MAX_SLIPPAGE_PIPS", "2.0"))

    symbols: list = field(default_factory=lambda: _list_env("SYMBOLS", "EURUSD,GBPUSD,USDJPY"))
    timeframes: list = field(default_factory=lambda: _list_env("TIMEFRAMES", "M5,M15,H1"))

    api_host: str = os.getenv("API_HOST", "0.0.0.0")
    api_port: int = int(os.getenv("API_PORT", "8000"))
    api_auth_token: str = os.getenv("API_AUTH_TOKEN", "change-me")


settings = Settings()
