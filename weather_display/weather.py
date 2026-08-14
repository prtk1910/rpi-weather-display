from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

import requests

from .state import Settings, StateStore


FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"


class WeatherError(RuntimeError):
    pass


@dataclass(frozen=True)
class WeatherSnapshot:
    fetched_at: str
    observed_at: str
    temperature: float
    apparent_temperature: float
    high: float
    low: float
    weather_code: int
    is_day: bool
    wind_speed: float
    wind_direction: float
    wind_gusts: float
    uv_index: float
    precipitation_probability: int
    temperature_unit: str
    wind_unit: str

    @classmethod
    def from_dict(cls, value: dict) -> "WeatherSnapshot":
        try:
            return cls(**{name: value[name] for name in cls.__dataclass_fields__})
        except (KeyError, TypeError, ValueError) as exc:
            raise WeatherError("invalid cached weather") from exc

    def to_dict(self) -> dict:
        return asdict(self)


class OpenMeteoProvider:
    def __init__(self, session: requests.Session | None = None, timeout: float = 8):
        self.session = session or requests.Session()
        self.timeout = timeout

    def fetch(self, settings: Settings) -> WeatherSnapshot:
        imperial = settings.units == "imperial"
        params = {
            "latitude": settings.latitude, "longitude": settings.longitude,
            "timezone": settings.timezone,
            "current": ",".join(("temperature_2m", "apparent_temperature", "weather_code",
                                  "is_day", "wind_speed_10m", "wind_direction_10m",
                                  "wind_gusts_10m", "uv_index")),
            "hourly": "precipitation_probability",
            "daily": "temperature_2m_max,temperature_2m_min",
            "forecast_days": 1,
            "temperature_unit": "fahrenheit" if imperial else "celsius",
            "wind_speed_unit": "mph" if imperial else "kmh",
        }
        try:
            response = self.session.get(FORECAST_URL, params=params, timeout=self.timeout)
            response.raise_for_status()
            body = response.json()
            current = body["current"]
            daily = body["daily"]
            hourly = body["hourly"]
            index = _hour_index(hourly["time"], current["time"])
            return WeatherSnapshot(
                fetched_at=datetime.now(timezone.utc).isoformat(), observed_at=str(current["time"]),
                temperature=float(current["temperature_2m"]),
                apparent_temperature=float(current["apparent_temperature"]),
                high=float(daily["temperature_2m_max"][0]), low=float(daily["temperature_2m_min"][0]),
                weather_code=int(current["weather_code"]), is_day=bool(current["is_day"]),
                wind_speed=float(current["wind_speed_10m"]),
                wind_direction=float(current["wind_direction_10m"]),
                wind_gusts=float(current["wind_gusts_10m"]), uv_index=float(current["uv_index"]),
                precipitation_probability=int(hourly["precipitation_probability"][index]),
                temperature_unit="°F" if imperial else "°C", wind_unit="mph" if imperial else "km/h",
            )
        except (requests.RequestException, KeyError, IndexError, TypeError, ValueError) as exc:
            raise WeatherError("weather service returned no usable data") from exc

    def search_locations(self, query: str, count: int = 8) -> list[dict[str, Any]]:
        if not query.strip():
            return []
        try:
            response = self.session.get(GEOCODING_URL, params={"name": query, "count": count,
                                        "language": "en", "format": "json"}, timeout=self.timeout)
            response.raise_for_status()
            results = response.json().get("results", [])
            return [{
                "label": ", ".join(str(part) for part in (item.get("name"), item.get("admin1"),
                                                            item.get("country")) if part),
                "latitude": item["latitude"], "longitude": item["longitude"],
                "timezone": item["timezone"],
            } for item in results if all(key in item for key in ("latitude", "longitude", "timezone"))]
        except (requests.RequestException, ValueError, TypeError) as exc:
            raise WeatherError("location search unavailable") from exc


def _hour_index(times: list[str], current_time: str) -> int:
    hour = str(current_time)[:13]
    for index, value in enumerate(times):
        if str(value)[:13] == hour:
            return index
    raise ValueError("current hour missing")


class WeatherService:
    """Fetches weather while preserving the last successful snapshot."""
    def __init__(self, store: StateStore, provider: OpenMeteoProvider):
        self.store, self.provider = store, provider
        self.snapshot: WeatherSnapshot | None = None
        self.last_error: str | None = None
        cached = store.load_cache()
        if cached:
            try: self.snapshot = WeatherSnapshot.from_dict(cached)
            except WeatherError: pass

    def refresh(self, settings: Settings) -> bool:
        try:
            new = self.provider.fetch(settings)
            changed = new != self.snapshot
            self.snapshot, self.last_error = new, None
            self.store.save_cache(new.to_dict())
            return changed
        except WeatherError as exc:
            self.last_error = str(exc)
            return False
