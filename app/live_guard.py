"""
Live-trading guard.

Live trading is supported, but it is deliberate and hard to trigger by
accident. Real orders are only possible when ALL of these hold, and each layer
(config validation here, the MT5 gateway, the execution engine, the risk
engine) re-checks independently:

  1. ACCOUNT_MODE=live and GATEWAY_MODE=mt5 (the mock gateway can never go live)
  2. LIVE_TRADING_ACK equals LIVE_ACK_PHRASE exactly
  3. LIVE_EXPECTED_LOGIN is set, equals MT5_LOGIN, and equals the login the
     terminal reports (so a wrong account or a typo cannot trade)
  4. The broker reports the account as REAL (not demo, not contest)
  5. API_AUTH_TOKEN has been changed from the default
  6. Positive hard caps exist (per-order lots, orders per day)

Demo mode is the reverse: it refuses to run against a real account, so a demo
config pointed at a live login by mistake stops too.

These functions are pure (they take the settings object as an argument) so the
same logic is used by every layer and is easy to test.
"""
from __future__ import annotations

from app.config import settings

LIVE_ACK_PHRASE = "I_UNDERSTAND_THIS_TRADES_REAL_MONEY"
_DEFAULT_TOKENS = {"", "change-me", "changeme"}


class LiveTradingConfigError(RuntimeError):
    """Live mode was requested but the configuration does not satisfy the guard."""


def is_live(cfg=None) -> bool:
    return (cfg or settings).account_mode == "live"


def live_config_problems(cfg=None) -> list[str]:
    """Everything wrong with the live configuration (empty list = valid).
    Only meaningful when account_mode == 'live'."""
    cfg = cfg or settings
    problems: list[str] = []
    if cfg.gateway_mode != "mt5":
        problems.append("GATEWAY_MODE must be 'mt5' for live trading (the mock gateway cannot trade real money)")
    if cfg.live_trading_ack != LIVE_ACK_PHRASE:
        problems.append(f"LIVE_TRADING_ACK must be exactly {LIVE_ACK_PHRASE}")
    if not cfg.live_expected_login:
        problems.append("LIVE_EXPECTED_LOGIN must be set to the one real account login this deployment may trade")
    elif cfg.mt5_login != cfg.live_expected_login:
        problems.append("MT5_LOGIN does not match LIVE_EXPECTED_LOGIN")
    if cfg.api_auth_token in _DEFAULT_TOKENS or len(cfg.api_auth_token) < 16:
        problems.append("API_AUTH_TOKEN must be changed from the default and be at least 16 characters")
    if not cfg.live_max_volume_lots > 0:
        problems.append("LIVE_MAX_VOLUME_LOTS must be > 0")
    if cfg.live_max_orders_per_day < 1:
        problems.append("LIVE_MAX_ORDERS_PER_DAY must be >= 1")
    if not (0 < cfg.live_risk_per_trade_pct <= 100):
        problems.append("LIVE_RISK_PER_TRADE_PCT must be in (0, 100]")
    if not (0 < cfg.live_max_daily_loss_pct <= 100):
        problems.append("LIVE_MAX_DAILY_LOSS_PCT must be in (0, 100]")
    return problems


def require_valid_live_config(cfg=None) -> None:
    cfg = cfg or settings
    problems = live_config_problems(cfg)
    if problems:
        raise LiveTradingConfigError("Live trading refused: " + "; ".join(problems))


def account_problems(cfg, *, login: str, is_demo: bool, is_real: bool) -> list[str]:
    """Check the account the broker actually reports against the configured mode.
    Empty list = this account may be traded in this mode."""
    mode = cfg.account_mode
    if mode == "demo":
        return [] if is_demo else [
            "ACCOUNT_MODE=demo but the connected account is not a demo account "
            "(set ACCOUNT_MODE=live deliberately, or connect a demo account)"]
    if mode == "live":
        problems = live_config_problems(cfg)
        if is_demo:
            problems.append("ACCOUNT_MODE=live but the connected account is a demo account")
        elif not is_real:
            problems.append("connected account is not reported as a REAL account by the broker (contest/other)")
        if str(login) != str(cfg.live_expected_login):
            problems.append(f"connected login {login} does not match LIVE_EXPECTED_LOGIN")
        return problems
    return [f"ACCOUNT_MODE={mode!r} is not valid (use 'demo' or 'live')"]


def effective_limits(cfg=None) -> dict:
    """Limits the risk engine applies: live values can only tighten the general ones."""
    cfg = cfg or settings
    if not is_live(cfg):
        return {"risk_per_trade_pct": cfg.risk_per_trade_pct,
                "max_daily_loss_pct": cfg.max_daily_loss_pct,
                "max_volume_lots": None, "max_orders_per_day": None}
    return {"risk_per_trade_pct": min(cfg.risk_per_trade_pct, cfg.live_risk_per_trade_pct),
            "max_daily_loss_pct": min(cfg.max_daily_loss_pct, cfg.live_max_daily_loss_pct),
            "max_volume_lots": cfg.live_max_volume_lots,
            "max_orders_per_day": cfg.live_max_orders_per_day}


def describe_mode(cfg=None) -> dict:
    """Safe-to-expose summary (no secrets) for banners and /health."""
    cfg = cfg or settings
    out = {"account_mode": cfg.account_mode, "gateway_mode": cfg.gateway_mode}
    if is_live(cfg):
        problems = live_config_problems(cfg)
        out.update(live_config_valid=not problems, live_config_problems=problems,
                   limits=effective_limits(cfg))
    return out
