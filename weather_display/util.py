from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo


WMO_CONDITIONS = {
    0: ("Clear", "clear"), 1: ("Mostly clear", "partly-cloudy"),
    2: ("Partly cloudy", "partly-cloudy"), 3: ("Overcast", "cloudy"),
    45: ("Fog", "fog"), 48: ("Rime fog", "fog"),
    51: ("Light drizzle", "rain"), 53: ("Drizzle", "rain"),
    55: ("Heavy drizzle", "rain"), 56: ("Freezing drizzle", "sleet"),
    57: ("Heavy freezing drizzle", "sleet"), 61: ("Light rain", "rain"),
    63: ("Rain", "rain"), 65: ("Heavy rain", "rain"),
    66: ("Freezing rain", "sleet"), 67: ("Heavy freezing rain", "sleet"),
    71: ("Light snow", "snow"), 73: ("Snow", "snow"),
    75: ("Heavy snow", "snow"), 77: ("Snow grains", "snow"),
    80: ("Rain showers", "rain"), 81: ("Rain showers", "rain"),
    82: ("Heavy showers", "rain"), 85: ("Snow showers", "snow"),
    86: ("Heavy snow showers", "snow"), 95: ("Thunderstorm", "storm"),
    96: ("Thunderstorm with hail", "storm"), 99: ("Severe hail storm", "storm"),
}


def condition_for(code: int, is_day: bool = True) -> tuple[str, str]:
    label, icon = WMO_CONDITIONS.get(int(code), ("Unknown", "unknown"))
    if icon == "clear" and not is_day:
        icon = "clear-night"
    elif icon == "partly-cloudy" and not is_day:
        icon = "partly-cloudy-night"
    return label, icon


def compass_direction(degrees: float) -> str:
    points = ("N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
              "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW")
    return points[int((float(degrees) % 360 + 11.25) // 22.5) % 16]


def uv_risk(value: float) -> str:
    value = float(value)
    if value < 3: return "Low"
    if value < 6: return "Moderate"
    if value < 8: return "High"
    if value < 11: return "Very high"
    return "Extreme"


def rounded(value: float) -> int:
    """Round halves away from zero, avoiding Python's banker's rounding."""
    value = float(value)
    return int(value + 0.5) if value >= 0 else int(value - 0.5)


def local_datetime(at: datetime, timezone: str) -> datetime:
    if at.tzinfo is None:
        at = at.replace(tzinfo=ZoneInfo("UTC"))
    return at.astimezone(ZoneInfo(timezone))


def format_clock(at: datetime, timezone: str, clock_format: str) -> str:
    local = local_datetime(at, timezone)
    if clock_format == "12h":
        return local.strftime("%I:%M %p").lstrip("0")
    return local.strftime("%H:%M")


def age_label(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60: return "just now"
    minutes = seconds // 60
    if minutes < 60: return f"{minutes}m ago"
    hours = minutes // 60
    if hours < 24: return f"{hours}h ago"
    return f"{hours // 24}d ago"
