from datetime import datetime, timezone

import pytest

from weather_display.util import compass_direction, condition_for, format_clock, rounded, uv_risk


@pytest.mark.parametrize("code,label,icon", [(0, "Clear", "clear"), (45, "Fog", "fog"),
                                              (63, "Rain", "rain"), (95, "Thunderstorm", "storm"),
                                              (123, "Unknown", "unknown")])
def test_conditions(code, label, icon): assert condition_for(code) == (label, icon)

def test_night_icon_selection():
    assert condition_for(0, False)[1] == "clear-night"
    assert condition_for(2, False)[1] == "partly-cloudy-night"

@pytest.mark.parametrize("degrees,expected", [(0,"N"),(11,"N"),(12,"NNE"),(90,"E"),(225,"SW"),(359,"N"),(-90,"W")])
def test_compass(degrees, expected): assert compass_direction(degrees) == expected

@pytest.mark.parametrize("value,expected", [(0,"Low"),(2.9,"Low"),(3,"Moderate"),(6,"High"),(8,"Very high"),(11,"Extreme")])
def test_uv(value, expected): assert uv_risk(value) == expected

def test_rounding_away_from_zero():
    assert rounded(2.5) == 3
    assert rounded(-2.5) == -3

def test_clock_uses_location_timezone():
    at = datetime(2026, 1, 15, 20, 5, tzinfo=timezone.utc)
    assert format_clock(at, "America/Los_Angeles", "24h") == "12:05"
    assert format_clock(at, "America/Los_Angeles", "12h") == "12:05 PM"
