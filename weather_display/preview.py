from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from .events import Event, EventSelection
from .renderer import save_event_preview, save_preview
from .state import Settings
from .weather import WeatherSnapshot


NOW = datetime(2026, 8, 14, 19, 24, tzinfo=timezone.utc)


def sample(scenario: str) -> tuple[Settings, WeatherSnapshot | None, str | None]:
    settings = Settings()
    base = WeatherSnapshot(
        fetched_at=(NOW - timedelta(minutes=4)).isoformat(), observed_at="2026-08-14T12:15",
        temperature=18.4, apparent_temperature=17.8, high=21.2, low=13.7,
        weather_code=1, is_day=True, wind_speed=14.2, wind_direction=248,
        wind_gusts=25.1, uv_index=5.4, precipitation_probability=8,
        temperature_unit="°C", wind_unit="km/h")
    error = None
    if scenario == "night": base = replace(base, weather_code=0, is_day=False)
    elif scenario == "rain": base = replace(base, weather_code=63, precipitation_probability=84)
    elif scenario == "fog": base = replace(base, weather_code=45)
    elif scenario == "extreme": base = replace(base, temperature=47.8, apparent_temperature=52.2, high=49.1, low=35.4)
    elif scenario == "long-location": settings = replace(settings, location_label="A Very Long Neighborhood Name By The Bay")
    elif scenario == "stale": base = replace(base, fetched_at=(NOW - timedelta(hours=3)).isoformat()); error = "offline"
    elif scenario == "no-data": return settings, None, "offline"
    return settings, base, error


def event_sample(scenario: str) -> tuple[Settings, EventSelection, str | None, str | None]:
    def event(title: str, hour: int, venue: str, day: int = 14) -> Event:
        start = datetime(2026, 8, day, hour, 0, tzinfo=timezone(timedelta(hours=-7)))
        return Event(title, f"https://sf.funcheap.com/{day}-{hour}-{len(title)}/",
                     start.isoformat(), (start + timedelta(hours=2)).isoformat(), venue,
                     ("Top Pick", "Art & Museums"))
    today = (
        event("Free Rooftop Concert Downtown", 14, "Yerba Buena Gardens"),
        event("Friday Night at the Museums", 18, "de Young Museum"),
        event("Outdoor Movie Under the Stars", 20, "Mission Bay Commons"),
    )
    weekend = (
        event("Golden Gate Park Art Festival", 10, "Hall of Flowers", 15),
        event("Neighborhood Food and Music Fair", 13, "Kern & Diamond", 15),
        event("Sunday Waterfront Makers Market", 11, "Ferry Building", 16),
    )
    if scenario == "events-long":
        today = (event("An Exceptionally Long San Francisco Event Title That Must Fit Gracefully", 14,
                       "An Unusually Long Venue Name Near the Waterfront"), *today[1:])
    if scenario == "events-unavailable":
        return Settings(), EventSelection((), ()), None, "offline"
    fetched = (NOW - timedelta(hours=3 if scenario == "events-stale" else 0, minutes=4)).isoformat()
    return Settings(), EventSelection(today, weekend), fetched, "offline" if scenario == "events-stale" else None


def main() -> None:
    parser = argparse.ArgumentParser(description="Render a deterministic 480×320 dashboard preview")
    parser.add_argument("--scenario", choices=("day", "night", "rain", "fog", "extreme", "long-location", "stale", "no-data",
                                               "events", "events-long", "events-stale", "events-unavailable"), default="day")
    parser.add_argument("--output", default="examples/dashboard.png")
    args = parser.parse_args()
    if args.scenario.startswith("events"):
        settings, selection, fetched_at, error = event_sample(args.scenario)
        save_event_preview(args.output, settings, selection, NOW, fetched_at, error)
    else:
        settings, weather, error = sample(args.scenario)
        save_preview(args.output, settings, weather, NOW, error)


if __name__ == "__main__": main()
