"""
News risk service — blueprint section 12 (news/economic intelligence), scoped
to what the deterministic Risk Engine actually needs: "is a market-moving
event close enough that we should not open a new trade right now?"

Output is a score in [0, 1] plus a human-readable reason, consumed through
AccountState.news_risk_score / news_reason. The Risk Engine rejects new
trades at >= 0.8 (see app/risk_engine.py). This module is deterministic —
no model calls, no randomness — so the decision stays auditable.

Scoring, per matching event (currency of the symbol, or "ALL"):
  * impact weight: HIGH = 1.0, MEDIUM = 0.5, LOW/HOLIDAY = 0.0
    (MEDIUM alone never blocks; it only shows up in the audit trail.)
  * inside the window [event - before, event + after]  -> full weight
  * outside it, linear decay to 0 over `ramp_min` more minutes
  The symbol's score is the maximum over its events.

Fail-safe: if a provider is configured but there is no usable data (never
fetched, or older than `stale_after_min`), behaviour follows `fail_mode`:
"closed" (default) scores 1.0 with reason "news_feed_unavailable" so no new
trades open blind; "open" scores 0.0 and logs a warning.

This module never opens or closes anything. It only informs the Risk Engine.
"""
from __future__ import annotations
import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app.news.events import ALL_CURRENCIES, EconomicEvent, currencies_for_symbol
from app.news.providers import NullProvider, provider_from_settings

log = logging.getLogger("fx.news")

IMPACT_WEIGHT = {"HIGH": 1.0, "MEDIUM": 0.5, "LOW": 0.0, "HOLIDAY": 0.0}
RETRY_AFTER_FAILURE = timedelta(seconds=60)


@dataclass(frozen=True)
class NewsRisk:
    score: float = 0.0
    reason: str = ""


def score_event(event: EconomicEvent, now: datetime, before_min: float, after_min: float,
                ramp_min: float) -> float:
    weight = IMPACT_WEIGHT.get(event.impact, 0.0)
    if weight <= 0:
        return 0.0
    minutes_to_event = (event.time_utc - now).total_seconds() / 60.0   # >0 = upcoming
    if -after_min <= minutes_to_event <= before_min:
        return weight
    if minutes_to_event > before_min:
        distance = minutes_to_event - before_min
    else:
        distance = (-minutes_to_event) - after_min
    if ramp_min <= 0 or distance >= ramp_min:
        return 0.0
    return weight * (1.0 - distance / ramp_min)


class NewsService:
    def __init__(self, provider, *, before_min=30.0, after_min=15.0, ramp_min=30.0,
                 refresh_min=30.0, stale_after_min=360.0, fail_mode="closed"):
        if fail_mode not in ("closed", "open"):
            raise ValueError("fail_mode must be 'closed' or 'open'")
        self.provider = provider
        self.before_min, self.after_min, self.ramp_min = before_min, after_min, ramp_min
        self.refresh = timedelta(minutes=refresh_min)
        self.stale_after = timedelta(minutes=stale_after_min)
        self.fail_mode = fail_mode
        self._events: list = []
        self._loaded_at = None        # last successful fetch
        self._next_attempt = None     # earliest time of the next fetch attempt
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return not isinstance(self.provider, NullProvider)

    def _refresh_if_due(self, now: datetime) -> None:
        with self._lock:
            if self._next_attempt and now < self._next_attempt:
                return
            if self._loaded_at and now - self._loaded_at < self.refresh:
                return
            try:
                result = self.provider.fetch()
                self._events, self._loaded_at = result.events, now
                self._next_attempt = now + self.refresh
                if result.skipped:
                    log.warning("news feed: skipped %d malformed rows", result.skipped)
            except Exception as e:  # noqa: BLE001 - any provider failure is handled by fail_mode
                self._next_attempt = now + RETRY_AFTER_FAILURE
                log.warning("news feed fetch failed (%s: %s)", type(e).__name__, e)

    def risk_for(self, symbol: str, now: datetime | None = None) -> NewsRisk:
        if not self.enabled:
            return NewsRisk()
        now = now or datetime.now(timezone.utc)
        self._refresh_if_due(now)

        if self._loaded_at is None or now - self._loaded_at > self.stale_after:
            if self.fail_mode == "closed":
                return NewsRisk(1.0, "news_feed_unavailable")
            log.warning("news data unavailable/stale; fail_mode=open so trading continues")
            return NewsRisk()

        currencies = currencies_for_symbol(symbol)
        best_score, best_event = 0.0, None
        for ev in self._events:
            if ev.currency != ALL_CURRENCIES and ev.currency not in currencies:
                continue
            s = score_event(ev, now, self.before_min, self.after_min, self.ramp_min)
            if s > best_score:
                best_score, best_event = s, ev
        if best_event is None:
            return NewsRisk()
        mins = round((best_event.time_utc - now).total_seconds() / 60.0)
        return NewsRisk(round(best_score, 3),
                        f"news:{best_event.currency}:{best_event.impact}:{best_event.title}:{mins:+d}min")


_service: NewsService | None = None


def get_news_service() -> NewsService:
    """Process-wide service built from app.config.settings on first use."""
    global _service
    if _service is None:
        from app.config import settings
        _service = NewsService(
            provider_from_settings(settings),
            before_min=settings.news_block_before_min, after_min=settings.news_block_after_min,
            ramp_min=settings.news_ramp_min, refresh_min=settings.news_refresh_min,
            stale_after_min=settings.news_stale_after_min, fail_mode=settings.news_fail_mode,
        )
    return _service


def set_news_service(service: NewsService | None) -> None:
    """Replace (or reset with None) the process-wide service. Used by tests."""
    global _service
    _service = service
