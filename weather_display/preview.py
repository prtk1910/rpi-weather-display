from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from .renderer import save_preview
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


def main() -> None:
    parser = argparse.ArgumentParser(description="Render a deterministic 480×320 dashboard preview")
    parser.add_argument("--scenario", choices=("day", "night", "rain", "fog", "extreme", "long-location", "stale", "no-data"), default="day")
    parser.add_argument("--output", default="examples/dashboard.png")
    args = parser.parse_args()
    settings, weather, error = sample(args.scenario)
    save_preview(args.output, settings, weather, NOW, error)


if __name__ == "__main__": main()
