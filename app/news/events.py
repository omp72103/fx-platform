"""
Economic calendar events and feed parsing.

Feed format (the public weekly calendar JSON that many retail tools consume):

    [{"title": "Core CPI m/m", "country": "USD",
      "date": "2026-10-14T08:30:00-04:00", "impact": "High",
      "forecast": "0.2%", "previous": "0.3%"}, ...]

`country` is really a currency code (USD, EUR, ...) and may be "All" for
events that affect everything (e.g. IMF meetings). `impact` is one of
High / Medium / Low / Holiday.

Parsing is deliberately strict about what it needs (a parseable timestamp and
a currency) and lenient about the rest: a malformed row is skipped and counted,
never allowed to take the whole feed down.
"""
from __future__ import annotations
import json
from dataclasses import dataclass
from datetime import datetime, timezone

IMPACTS = ("HIGH", "MEDIUM", "LOW", "HOLIDAY")
ALL_CURRENCIES = "ALL"


@dataclass(frozen=True)
class EconomicEvent:
    title: str
    currency: str            # upper-case ISO-style code, or "ALL"
    time_utc: datetime       # timezone-aware, UTC
    impact: str              # HIGH / MEDIUM / LOW / HOLIDAY
    forecast: str = ""
    previous: str = ""


@dataclass
class ParseResult:
    events: list
    skipped: int = 0


def _parse_time(value: str) -> datetime:
    dt = datetime.fromisoformat(value.strip())
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)  # no offset given: assume UTC, never local time
    return dt.astimezone(timezone.utc)


def parse_events(rows) -> ParseResult:
    events, skipped = [], 0
    for row in rows or []:
        try:
            currency = str(row["country"]).strip().upper()
            impact = str(row.get("impact", "")).strip().upper()
            if not currency or impact not in IMPACTS:
                raise ValueError("missing currency or unknown impact")
            events.append(EconomicEvent(
                title=str(row.get("title", "")).strip(),
                currency=currency, time_utc=_parse_time(str(row["date"])), impact=impact,
                forecast=str(row.get("forecast", "") or ""), previous=str(row.get("previous", "") or ""),
            ))
        except (KeyError, ValueError, TypeError, AttributeError):
            skipped += 1
    return ParseResult(events=events, skipped=skipped)


def parse_feed_json(text: str) -> ParseResult:
    data = json.loads(text)
    if not isinstance(data, list):
        raise ValueError("calendar feed must be a JSON list of events")
    return parse_events(data)


def currencies_for_symbol(symbol: str) -> set:
    """EURUSD -> {EUR, USD}; XAUUSD -> {XAU, USD}. Symbols shorter than 6
    characters are treated as a single code so a typo can't silently become
    'no news risk'."""
    s = symbol.strip().upper()
    if len(s) >= 6:
        return {s[:3], s[3:6]}
    return {s}
