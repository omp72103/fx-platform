"""
Central configuration, loaded from environment variables (.env).
Nothing here should ever contain a hard-coded secret (section 25).
"""

from __future__ import annotations
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

    # "demo" (default) or "live". Live trading is opt-in and guarded: see
    # app/live_guard.py for everything that must be true before a single real
    # order can be sent. Any other value blocks all orders.
    account_mode: str = os.getenv("ACCOUNT_MODE", "demo")

    # --- Live-mode guards (only consulted when ACCOUNT_MODE=live) ---------------
    # Must equal app.live_guard.LIVE_ACK_PHRASE exactly.
    live_trading_ack: str = os.getenv("LIVE_TRADING_ACK", "")
    # The ONE real account login this deployment may trade. Must match MT5_LOGIN
    # and the login the terminal actually reports.
    live_expected_login: str = os.getenv("LIVE_EXPECTED_LOGIN", "")
    # Hard per-order volume cap in lots (raise it deliberately, later).
    live_max_volume_lots: float = float(os.getenv("LIVE_MAX_VOLUME_LOTS", "0.01"))
    live_max_orders_per_day: int = int(os.getenv("LIVE_MAX_ORDERS_PER_DAY", "3"))
    # Live limits can only tighten the general ones (the stricter value wins).
    live_risk_per_trade_pct: float = float(os.getenv("LIVE_RISK_PER_TRADE_PCT", "0.25"))
    live_max_daily_loss_pct: float = float(os.getenv("LIVE_MAX_DAILY_LOSS_PCT", "1.0"))
    # Automated strategies must be status "approved" in the registry to trade live.
    live_require_approved_strategy: bool = os.getenv("LIVE_REQUIRE_APPROVED_STRATEGY", "true").lower() != "false"

    risk_per_trade_pct: float = float(os.getenv("RISK_PER_TRADE_PCT", "1.0"))
    max_daily_loss_pct: float = float(os.getenv("MAX_DAILY_LOSS_PCT", "3.0"))
    max_weekly_loss_pct: float = float(os.getenv("MAX_WEEKLY_LOSS_PCT", "6.0"))
    max_drawdown_pct: float = float(os.getenv("MAX_DRAWDOWN_PCT", "25.0"))
    max_open_positions: int = int(os.getenv("MAX_OPEN_POSITIONS", "5"))
    max_symbol_exposure_pct: float = float(os.getenv("MAX_SYMBOL_EXPOSURE_PCT", "10.0"))
    max_portfolio_exposure_pct: float = float(os.getenv("MAX_PORTFOLIO_EXPOSURE_PCT", "25.0"))
    max_spread_pips: float = float(os.getenv("MAX_SPREAD_PIPS", "3.0"))
    max_slippage_pips: float = float(os.getenv("MAX_SLIPPAGE_PIPS", "2.0"))

    # --- News / economic calendar risk (blueprint section 12) ------------------
    # none          = disabled (news_risk_score is always 0.0)
    # forexfactory  = public weekly calendar JSON (unofficial, best-effort)
    # file          = a local JSON file in the same format (NEWS_CALENDAR_PATH)
    news_provider: str = os.getenv("NEWS_PROVIDER", "none")
    news_calendar_path: str = os.getenv("NEWS_CALENDAR_PATH", "")
    news_feed_url: str = os.getenv("NEWS_FEED_URL",
                                    "https://nfs.faireconomy.media/ff_calendar_thisweek.json")
    news_block_before_min: float = float(os.getenv("NEWS_BLOCK_BEFORE_MIN", "30"))
    news_block_after_min: float = float(os.getenv("NEWS_BLOCK_AFTER_MIN", "15"))
    news_ramp_min: float = float(os.getenv("NEWS_RAMP_MIN", "30"))
    news_refresh_min: float = float(os.getenv("NEWS_REFRESH_MIN", "30"))
    news_stale_after_min: float = float(os.getenv("NEWS_STALE_AFTER_MIN", "360"))
    # What to do when a provider is configured but its data is missing/stale:
    # "closed" blocks new trades (safe default), "open" trades anyway (logs a warning).
    news_fail_mode: str = os.getenv("NEWS_FAIL_MODE", "closed")

    symbols: list = field(default_factory=lambda: _list_env("SYMBOLS", "EURUSD,GBPUSD,USDJPY"))
    timeframes: list = field(default_factory=lambda: _list_env("TIMEFRAMES", "M5,M15,H1"))

    api_host: str = os.getenv("API_HOST", "0.0.0.0")
    api_port: int = int(os.getenv("API_PORT", "8000"))
    api_auth_token: str = os.getenv("API_AUTH_TOKEN", "change-me")


settings = Settings()
