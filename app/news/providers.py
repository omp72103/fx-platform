"""
Calendar providers. Each returns a ParseResult from `fetch()` and raises on
failure — the service decides what a failure means for trading.

NOTE on ForexFactoryProvider: the weekly JSON it reads is a public but
UNOFFICIAL feed. It's fine for demo/paper trading and research. Before
trading real money, swap in a licensed calendar provider by adding another
class here with the same `fetch()` contract — nothing else needs to change.
"""
from __future__ import annotations
import urllib.request
from pathlib import Path

from app.news.events import ParseResult, parse_feed_json


class NullProvider:
    """News risk disabled (NEWS_PROVIDER=none)."""
    name = "none"

    def fetch(self) -> ParseResult:
        return ParseResult(events=[])


class FileProvider:
    """Reads a local JSON file in the feed format. Useful for offline runs,
    tests, and for hand-maintained calendars (e.g. a central-bank meeting
    schedule you trust more than any feed)."""
    name = "file"

    def __init__(self, path: str):
        if not path:
            raise ValueError("NEWS_PROVIDER=file requires NEWS_CALENDAR_PATH")
        self.path = path

    def fetch(self) -> ParseResult:
        return parse_feed_json(Path(self.path).read_text(encoding="utf-8"))


class ForexFactoryProvider:
    name = "forexfactory"

    def __init__(self, url: str, timeout_s: float = 10.0):
        self.url = url
        self.timeout_s = timeout_s

    def fetch(self) -> ParseResult:
        req = urllib.request.Request(self.url, headers={"User-Agent": "fx-platform/1.0"})
        with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:  # noqa: S310 - fixed https URL from config
            return parse_feed_json(resp.read().decode("utf-8"))


def provider_from_settings(settings):
    kind = (settings.news_provider or "none").strip().lower()
    if kind == "none":
        return NullProvider()
    if kind == "file":
        return FileProvider(settings.news_calendar_path)
    if kind == "forexfactory":
        return ForexFactoryProvider(settings.news_feed_url)
    raise ValueError(f"Unknown NEWS_PROVIDER {settings.news_provider!r} (use none|file|forexfactory)")
