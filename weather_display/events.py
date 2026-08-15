from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from urllib.parse import urlsplit, urlunsplit
from zoneinfo import ZoneInfo

import requests

from .state import EVENT_CATEGORIES, Settings, StateStore


RSS_URL = "https://sf.funcheap.com/rss-date/"
DATE_URL = "https://sf.funcheap.com/{year:04d}/{month:02d}/{day:02d}/"
PACIFIC = ZoneInfo("America/Los_Angeles")
EVENT_REFRESH_SECONDS = 6 * 60 * 60
_CATEGORY_SLUGS = {
    "top-pick": "Top Pick",
    "art-museums": "Art & Museums",
    "charity-volunteering": "Charity & Volunteering",
    "club-dj": "Club / DJ",
    "comedy": "Comedy",
    "comedy-event-types-event": "Comedy",
    "eating-drinking": "Eating & Drinking",
    "fairs-festivals": "Fairs & Festivals",
    "free-stuff": "Free Stuff",
    "fun-games": "Fun & Games",
    "geek-event": "Geek Event",
    "kids-families": "Kids & Families",
    "lectures-workshops": "Lectures & Workshops",
    "literature": "Literature",
    "live-music-event": "Live Music",
    "movies": "Movies",
    "shopping-fashion": "Shopping & Fashion",
    "sports-fitness": "Sports & Fitness",
    "theater-performance": "Theater & Performance",
}


class EventError(RuntimeError):
    pass


@dataclass(frozen=True)
class Event:
    title: str
    url: str
    start: str
    end: str | None
    venue: str
    categories: tuple[str, ...]
    all_day: bool = False

    @classmethod
    def from_dict(cls, value: dict) -> "Event":
        try:
            categories = tuple(value["categories"])
            event = cls(
                title=str(value["title"]), url=str(value["url"]), start=str(value["start"]),
                end=str(value["end"]) if value.get("end") else None,
                venue=str(value["venue"]), categories=categories,
                all_day=bool(value.get("all_day", False)),
            )
            _parse_datetime(event.start)
            if event.end:
                if _parse_datetime(event.end) <= _parse_datetime(event.start):
                    raise ValueError
            if not event.title or not event.url or not event.venue:
                raise ValueError
            return event
        except (KeyError, TypeError, ValueError) as exc:
            raise EventError("invalid cached event") from exc

    def to_dict(self) -> dict:
        value = asdict(self)
        value["categories"] = list(self.categories)
        return value


@dataclass(frozen=True)
class EventSelection:
    today: tuple[Event, ...]
    weekend: tuple[Event, ...]


class FuncheapProvider:
    def __init__(self, session: requests.Session | None = None, timeout: float = 12):
        self.session = session or requests.Session()
        self.timeout = timeout

    def fetch_today(self) -> list[Event]:
        return parse_rss(self._get(RSS_URL))

    def fetch_date(self, value: date) -> list[Event]:
        return parse_date_page(self._get(DATE_URL.format(
            year=value.year, month=value.month, day=value.day)))

    def _get(self, url: str) -> str:
        try:
            response = self.session.get(url, timeout=self.timeout,
                                        headers={"User-Agent": "rpi-weather-display/1.1"})
            response.raise_for_status()
            return response.text
        except requests.RequestException as exc:
            raise EventError("Funcheap is unavailable") from exc


class EventService:
    """Refreshes independent date partitions while retaining each last good result."""

    def __init__(self, store: StateStore, provider: FuncheapProvider):
        self.store, self.provider = store, provider
        self.last_error: str | None = None
        self.last_fetched_at: str | None = None

    def refresh(self, now: datetime | None = None) -> bool:
        now = now or datetime.now(timezone.utc)
        local_date = now.astimezone(PACIFIC).date()
        targets: list[tuple[date, object]] = [(local_date, self.provider.fetch_today)]
        targets.extend((day, lambda day=day: self.provider.fetch_date(day))
                       for day in weekend_dates(local_date) if day > local_date)
        errors = []
        changed = False
        fetched_at = now.astimezone(timezone.utc).isoformat()
        for day, fetch in targets:
            try:
                events = fetch()
                value = {"date": day.isoformat(), "fetched_at": fetched_at,
                         "events": [event.to_dict() for event in events]}
                changed = value.get("events") != (self.store.load_event_cache(day.isoformat()) or {}).get("events") or changed
                self.store.save_event_cache(day.isoformat(), value)
            except EventError as exc:
                errors.append(f"{day.isoformat()}: {exc}")
        self.last_error = "; ".join(errors) or None
        self._update_cache_time(local_date)
        return changed

    def selection(self, settings: Settings, now: datetime | None = None) -> EventSelection:
        now = now or datetime.now(timezone.utc)
        local_date = now.astimezone(PACIFIC).date()
        today_events = self._load_date(local_date)
        weekend_events = []
        for day in weekend_dates(local_date):
            weekend_events.extend(self._load_date(day))
        self._update_cache_time(local_date)
        today = _rank(today_events, settings.event_categories, now, dates={local_date})[:3]
        used = {_identity(event) for event in today}
        weekend = tuple(event for event in _rank(weekend_events, settings.event_categories, now,
                                                dates=set(weekend_dates(local_date)))
                        if _identity(event) not in used)[:3]
        return EventSelection(tuple(today), weekend)

    def cache_age_seconds(self, now: datetime | None = None) -> int | None:
        if not self.last_fetched_at:
            return None
        now = now or datetime.now(timezone.utc)
        return max(0, int((now - _parse_datetime(self.last_fetched_at)).total_seconds()))

    def seconds_until_refresh(self, now: datetime | None = None,
                              interval: int = EVENT_REFRESH_SECONDS) -> float:
        """Return zero when any currently needed date partition is absent or due."""
        now = now or datetime.now(timezone.utc)
        local_date = now.astimezone(PACIFIC).date()
        fetched = []
        for day in _refresh_dates(local_date):
            value = self.store.load_event_cache(day.isoformat()) or {}
            if not isinstance(value.get("events"), list):
                return 0
            try:
                fetched.append(_parse_datetime(value["fetched_at"]))
            except (KeyError, TypeError, ValueError):
                return 0
        self.last_fetched_at = min(fetched).isoformat() if fetched else None
        age = max(0.0, (now - min(fetched)).total_seconds())
        return max(0.0, interval - age)

    def _load_date(self, day: date) -> list[Event]:
        cached = self.store.load_event_cache(day.isoformat())
        if not cached or not isinstance(cached.get("events"), list):
            return []
        result = []
        for value in cached["events"]:
            try:
                result.append(Event.from_dict(value))
            except EventError:
                continue
        return result

    def _update_cache_time(self, local_date: date) -> None:
        fetched = []
        for day in {local_date, *weekend_dates(local_date)}:
            value = self.store.load_event_cache(day.isoformat()) or {}
            try:
                _parse_datetime(value["fetched_at"])
                fetched.append(value["fetched_at"])
            except (KeyError, TypeError, ValueError):
                pass
        # A combined scene is only as fresh as its oldest contributing partition.
        self.last_fetched_at = min(fetched, key=_parse_datetime) if fetched else None


def weekend_dates(today: date) -> tuple[date, date]:
    saturday = (today - timedelta(days=1) if today.weekday() == 6 else
                today + timedelta(days=(5 - today.weekday()) % 7))
    return saturday, saturday + timedelta(days=1)


def _refresh_dates(today: date) -> tuple[date, ...]:
    return (today, *(day for day in weekend_dates(today) if day > today))


def parse_rss(source: str | bytes) -> list[Event]:
    try:
        root = ET.fromstring(source)
    except ET.ParseError as exc:
        raise EventError("Funcheap RSS was malformed") from exc
    result = []
    for item in root.findall("./channel/item"):
        fields = {_local(child.tag): child for child in item}
        raw_categories = (_clean(value) for value in _text(fields.get("categories")).split(","))
        categories = tuple("Top Pick" if value == "*Top Pick*" else value for value in raw_categories
                           if value in EVENT_CATEGORIES or value in ("*Top Pick*", "Sponsored"))
        address = _clean(_text(fields.get("venueAddress")))
        venue = _clean(_text(fields.get("venue")))
        if "Sponsored" in categories or not _is_sf(address, venue):
            continue
        try:
            title = _clean_title(_text(fields.get("title")))
            url = _canonical_url(_text(fields.get("guid")) or _text(fields.get("link")))
            start = parsedate_to_datetime(_text(fields.get("startTime"))).isoformat()
            end_text = _text(fields.get("endTime"))
            end = parsedate_to_datetime(end_text).isoformat() if end_text else None
            all_day = (fields.get("allDay") is not None and
                       fields["allDay"].attrib.get("value", "").lower() == "true")
            event = Event(title, url, start, end, venue,
                          tuple(value for value in categories if value != "Sponsored"), all_day)
            if title and url and venue and (not end or _parse_datetime(end) > _parse_datetime(start)):
                result.append(event)
        except (TypeError, ValueError, OverflowError):
            continue
    return _deduplicate(result)


class _DatePageParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.depth = 0
        self.current: dict | None = None
        self.title_depth: int | None = None
        self.meta_depth: int | None = None
        self.events: list[Event] = []

    def handle_starttag(self, tag: str, attrs_list: list[tuple[str, str | None]]) -> None:
        attrs = dict(attrs_list)
        classes = set((attrs.get("class") or "").split())
        if self.current is None and tag == "div" and (attrs.get("id") or "").startswith("post-") and "tanbox" in classes:
            self.current = {"classes": classes, "title": [], "meta": [], "url": "",
                            "start": "", "end": "", "all_day": False}
            self.depth = 1
            return
        if self.current is None:
            return
        if tag == "div":
            self.depth += 1
        if tag == "span" and {"title", "entry-title"}.issubset(classes):
            self.title_depth = self.depth
        if tag == "div" and "date-time" in classes:
            self.meta_depth = self.depth
            self.current["start"] = attrs.get("data-event-date") or ""
            self.current["end"] = attrs.get("data-event-date-end") or ""
        if tag == "a" and self.title_depth is not None and not self.current["url"]:
            self.current["url"] = attrs.get("href") or ""

    def handle_data(self, data: str) -> None:
        if self.current is None:
            return
        if self.title_depth is not None:
            self.current["title"].append(data)
        if self.meta_depth is not None:
            self.current["meta"].append(data)
            if "All Day" in data:
                self.current["all_day"] = True

    def handle_endtag(self, tag: str) -> None:
        if self.current is None:
            return
        if tag == "span" and self.title_depth == self.depth:
            self.title_depth = None
        if tag == "div":
            if self.meta_depth == self.depth:
                self.meta_depth = None
            self.depth -= 1
            if self.depth == 0:
                event = _event_from_page_block(self.current)
                if event:
                    self.events.append(event)
                self.current = None


def parse_date_page(source: str) -> list[Event]:
    parser = _DatePageParser()
    try:
        parser.feed(source)
        parser.close()
    except (ValueError, TypeError) as exc:
        raise EventError("Funcheap date page was malformed") from exc
    if not parser.events and "id=\"post-" not in source and "id='post-" not in source:
        raise EventError("Funcheap date page contained no event data")
    return _deduplicate(parser.events)


def _event_from_page_block(value: dict) -> Event | None:
    classes = value["classes"]
    if "category-sponsored" in classes or not _page_is_sf(classes):
        return None
    categories = tuple(category for slug, category in _CATEGORY_SLUGS.items()
                       if f"category-{slug}" in classes)
    categories = tuple(dict.fromkeys(categories))
    meta = _clean(" ".join(value["meta"]))
    venue = _clean(meta.rsplit("|", 1)[-1]) if "|" in meta else ""
    try:
        start = datetime.strptime(value["start"], "%Y-%m-%d %H:%M").replace(tzinfo=PACIFIC).isoformat()
        end = (datetime.strptime(value["end"], "%Y-%m-%d %H:%M").replace(tzinfo=PACIFIC).isoformat()
               if value["end"] else None)
        title = _clean_title(" ".join(value["title"]))
        url = _canonical_url(value["url"])
        if not title or not url or not venue:
            return None
        if end and _parse_datetime(end) <= _parse_datetime(start):
            return None
        return Event(title, url, start, end, venue, categories, value["all_day"])
    except (TypeError, ValueError):
        return None


def _rank(events: list[Event], selected: tuple[str, ...], now: datetime,
          dates: set[date]) -> tuple[Event, ...]:
    selected_set = set(selected)
    filtered = []
    for event in _deduplicate(events):
        start = _parse_datetime(event.start)
        expiry = _parse_datetime(event.end) if event.end else start
        if start.astimezone(PACIFIC).date() not in dates or expiry <= now:
            continue
        filtered.append(event)
    return tuple(sorted(filtered, key=lambda event: (
        not bool(selected_set.intersection(event.categories)),
        "Top Pick" not in event.categories,
        _parse_datetime(event.start), event.title.casefold(),
    )))


def _deduplicate(events: list[Event]) -> list[Event]:
    result, seen_urls, seen_titles = [], set(), set()
    for event in events:
        url = _canonical_url(event.url)
        title = _identity(event)
        if url in seen_urls or title in seen_titles:
            continue
        seen_urls.add(url)
        seen_titles.add(title)
        result.append(event)
    return result


def _identity(event: Event) -> str:
    return re.sub(r"\W+", "", event.title.casefold())


def _canonical_url(value: str) -> str:
    parts = urlsplit(_clean(value))
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return ""
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/") + "/", "", ""))


def _clean(value: str) -> str:
    return " ".join(html.unescape(value or "").split())


def _clean_title(value: str) -> str:
    return re.sub(r"^\s*(?:\d+(?:\.\d+)?|FREE)\s*-\s*", "", _clean(value), flags=re.I)


def _text(element: ET.Element | None) -> str:
    return "" if element is None or element.text is None else element.text


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _is_sf(address: str, venue: str) -> bool:
    combined = f"{address} {venue}".casefold()
    return "san francisco" in combined or "san franciso" in combined or "(sf)" in combined


def _page_is_sf(classes: set[str]) -> bool:
    return ("region-san-francisco" in classes or "category-san-francisco" in classes or
            any(item.startswith("category-") and item.endswith("-san-francisco") for item in classes))


def _parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
