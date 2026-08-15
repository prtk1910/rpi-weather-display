from dataclasses import replace
from datetime import date, datetime, timedelta, timezone

import pytest

from weather_display.events import (Event, EventError, EventService, FuncheapProvider,
                                    parse_date_page, parse_rss, weekend_dates)
from weather_display.state import Settings, StateStore


NOW = datetime(2026, 8, 14, 19, 0, tzinfo=timezone.utc)
PACIFIC = timezone(timedelta(hours=-7))


def rss_item(title="Museum Night", url="museum-night", start="Fri, 14 Aug 2026 18:00:00 -0700",
             end="Fri, 14 Aug 2026 20:00:00 -0700", venue="SF Museum",
             address="1 Main St, San Francisco, CA", categories="*Top Pick*,Art &amp; Museums",
             all_day="false"):
    return f"""<item><title>{title}</title><link>https://sf.funcheap.com/{url}/</link>
      <guid>https://sf.funcheap.com/{url}/</guid>
      <fc:startTime>{start}</fc:startTime><fc:endTime>{end}</fc:endTime>
      <fc:venue>{venue}</fc:venue><fc:venueAddress>{address}</fc:venueAddress>
      <fc:categories>{categories}</fc:categories><fc:allDay value="{all_day}"/></item>"""


def rss(*items):
    return '<rss xmlns:fc="https://sf.funcheap.com/rssfeed/"><channel>' + "".join(items) + "</channel></rss>"


def page_event(title="Festival", slug="festival", start="2026-08-15 10:00",
               end="2026-08-15 12:00", venue="Civic Center",
               classes="category-top-pick category-fairs-festivals region-san-francisco"):
    return f"""<div id="post-1" class="tanbox post {classes}">
      <span class="title entry-title"><a href="https://sf.funcheap.com/{slug}/">{title}</a></span>
      <div class="meta archive-meta date-time" data-event-date="{start}" data-event-date-end="{end}">
      Saturday - <span>10:00 am</span> | <span>Cost: FREE</span> | <span>{venue}</span></div></div>"""


def make_event(title, hour, day=14, categories=("Comedy",), end_hour=None, url=None):
    start = datetime(2026, 8, day, hour, tzinfo=PACIFIC)
    end = datetime(2026, 8, day, end_hour, tzinfo=PACIFIC) if end_hour is not None else start + timedelta(hours=1)
    return Event(title, url or f"https://sf.funcheap.com/{title.lower().replace(' ', '-')}/",
                 start.isoformat(), end.isoformat(), "SF Venue", categories)


def save_day(store, day, events, fetched=NOW):
    store.save_event_cache(day.isoformat(), {"date": day.isoformat(), "fetched_at": fetched.isoformat(),
                                             "events": [event.to_dict() for event in events]})


def test_parse_rss_uses_structured_fields_and_cleans_cost_prefix():
    event = parse_rss(rss(rss_item(title="0 - Museum &amp; Gallery Night", all_day="true")))[0]
    assert event.title == "Museum & Gallery Night"
    assert event.all_day is True
    assert event.categories == ("Top Pick", "Art & Museums")
    assert event.venue == "SF Museum"


def test_parse_rss_filters_sponsored_non_sf_and_malformed_and_deduplicates():
    source = rss(
        rss_item(),
        rss_item(title="Museum Night", url="other-occurrence"),
        rss_item(title="Ad", url="ad", categories="Comedy,Sponsored"),
        rss_item(title="Oakland", url="oakland", address="1 Broadway, Oakland, CA", venue="The Hall"),
        rss_item(title="Broken", url="broken", start="not-a-date"),
    )
    assert [event.title for event in parse_rss(source)] == ["Museum Night"]
    with pytest.raises(EventError):
        parse_rss("not xml")


def test_parse_future_page_uses_classes_and_machine_readable_time():
    result = parse_date_page(page_event())
    assert len(result) == 1
    assert result[0].categories == ("Top Pick", "Fairs & Festivals")
    assert result[0].start == "2026-08-15T10:00:00-07:00"
    assert result[0].venue == "Civic Center"


def test_provider_uses_funcheap_date_path_segments():
    class Response:
        text = page_event()

        def raise_for_status(self):
            pass

    class Session:
        def __init__(self):
            self.url = None

        def get(self, url, **_kwargs):
            self.url = url
            return Response()

    session = Session()
    assert FuncheapProvider(session).fetch_date(date(2026, 8, 15))
    assert session.url == "https://sf.funcheap.com/2026/08/15/"


def test_future_page_filters_sponsored_non_sf_and_malformed():
    source = (page_event(title="Ad", classes="category-sponsored region-san-francisco") +
              page_event(title="East Bay", classes="category-art-museums region-east-bay") +
              page_event(title="Broken", start="bad", classes="category-art-museums region-san-francisco"))
    assert parse_date_page(source) == []
    with pytest.raises(EventError):
        parse_date_page("<html><body>blocked</body></html>")


@pytest.mark.parametrize(("day", "expected"), [
    (date(2026, 8, 10), (date(2026, 8, 15), date(2026, 8, 16))),
    (date(2026, 8, 15), (date(2026, 8, 15), date(2026, 8, 16))),
    (date(2026, 8, 16), (date(2026, 8, 15), date(2026, 8, 16))),
    (date(2026, 8, 17), (date(2026, 8, 22), date(2026, 8, 23))),
])
def test_weekend_boundaries(day, expected):
    assert weekend_dates(day) == expected


def test_ranking_selected_then_top_pick_then_time_with_fallback(tmp_path):
    store = StateStore(tmp_path)
    events = [
        make_event("Unselected top", 13, categories=("Top Pick", "Comedy")),
        make_event("Selected later", 17, categories=("Art & Museums",)),
        make_event("Selected top", 18, categories=("Top Pick", "Art & Museums")),
        make_event("Selected soon", 16, categories=("Art & Museums",)),
    ]
    save_day(store, date(2026, 8, 14), events)
    service = EventService(store, object())
    settings = replace(Settings(), event_categories=("Art & Museums",))
    assert [event.title for event in service.selection(settings, NOW).today] == [
        "Selected top", "Selected soon", "Selected later"]


def test_expired_and_cross_section_duplicates_are_removed(tmp_path):
    store = StateStore(tmp_path)
    expired = make_event("Expired", 10, end_hour=11)
    live = make_event("Live", 11, end_hour=14)
    save_day(store, date(2026, 8, 14), [expired, live])
    duplicate = make_event("Live", 10, day=15, url="https://sf.funcheap.com/another/")
    weekend = make_event("Weekend", 12, day=15)
    save_day(store, date(2026, 8, 15), [duplicate, weekend])
    selection = EventService(store, object()).selection(Settings(), NOW)
    assert [item.title for item in selection.today] == ["Live"]
    assert [item.title for item in selection.weekend] == ["Weekend"]


class PartialProvider:
    def __init__(self, today):
        self.today = today

    def fetch_today(self):
        return self.today

    def fetch_date(self, day):
        if day.day == 15:
            raise EventError("day unavailable")
        return [make_event("Sunday", 12, day=day.day)]


def test_partial_refresh_preserves_failed_date_partition(tmp_path):
    store = StateStore(tmp_path)
    cached = make_event("Cached Saturday", 12, day=15)
    save_day(store, date(2026, 8, 15), [cached], NOW - timedelta(hours=2))
    service = EventService(store, PartialProvider([make_event("Fresh Friday", 14)]))
    assert service.refresh(NOW) is True
    assert "2026-08-15" in service.last_error
    selection = service.selection(Settings(), NOW)
    assert [event.title for event in selection.today] == ["Fresh Friday"]
    assert {event.title for event in selection.weekend} == {"Cached Saturday", "Sunday"}
    assert store.load_event_cache("2026-08-15")["events"][0]["title"] == "Cached Saturday"


def test_fresh_complete_cache_defers_network_refresh_for_six_hours(tmp_path):
    store = StateStore(tmp_path)
    for day in (date(2026, 8, 14), date(2026, 8, 15), date(2026, 8, 16)):
        save_day(store, day, [make_event(f"Event {day.day}", 20, day=day.day)], NOW)
    service = EventService(store, object())
    assert service.seconds_until_refresh(NOW) == 6 * 60 * 60
    assert service.seconds_until_refresh(NOW + timedelta(hours=2)) == 4 * 60 * 60
    assert service.seconds_until_refresh(NOW + timedelta(hours=6)) == 0


def test_missing_or_malformed_required_partition_refreshes_immediately(tmp_path):
    store = StateStore(tmp_path)
    save_day(store, date(2026, 8, 14), [make_event("Friday", 20)], NOW)
    save_day(store, date(2026, 8, 15), [make_event("Saturday", 20, day=15)], NOW)
    service = EventService(store, object())
    assert service.seconds_until_refresh(NOW) == 0
    store.save_event_cache("2026-08-16", {"fetched_at": NOW.isoformat(), "events": "bad"})
    assert service.seconds_until_refresh(NOW) == 0
