from datetime import datetime

import pytest
import requests

from weather_display.state import Settings, StateStore
from weather_display.weather import OpenMeteoProvider, WeatherError, WeatherService


BODY = {
    "current": {"time":"2026-08-14T12:15", "temperature_2m":18.4,
                "apparent_temperature":17.8, "weather_code":1, "is_day":1,
                "wind_speed_10m":14.2, "wind_direction_10m":248,
                "wind_gusts_10m":25.1, "uv_index":5.4},
    "daily": {"temperature_2m_max":[21.2], "temperature_2m_min":[13.7]},
    "hourly": {"time":["2026-08-14T11:00","2026-08-14T12:00"],
               "precipitation_probability":[4,8]},
}


class Response:
    def __init__(self, body=BODY): self.body = body
    def raise_for_status(self): pass
    def json(self): return self.body

class Session:
    def __init__(self, response=Response(), error=None): self.response,self.error,self.calls=response,error,[]
    def get(self, url, **kwargs):
        self.calls.append((url,kwargs))
        if self.error: raise self.error
        return self.response


@pytest.mark.parametrize("units,temp_unit,wind_unit,temp_param,wind_param", [
    ("metric","°C","km/h","celsius","kmh"), ("imperial","°F","mph","fahrenheit","mph")])
def test_fetch_both_unit_systems(units,temp_unit,wind_unit,temp_param,wind_param):
    session=Session(); result=OpenMeteoProvider(session).fetch(Settings(units=units))
    assert (result.temperature_unit,result.wind_unit)==(temp_unit,wind_unit)
    params=session.calls[0][1]["params"]
    assert params["temperature_unit"]==temp_param and params["wind_speed_unit"]==wind_param
    assert result.precipitation_probability == 8

@pytest.mark.parametrize("response,error", [(Response({}),None),(Response({"current":{}}),None),
                                               (None,requests.Timeout("slow"))])
def test_fetch_rejects_malformed_and_timeouts(response,error):
    with pytest.raises(WeatherError): OpenMeteoProvider(Session(response,error)).fetch(Settings())

def test_service_persists_success_and_falls_back_to_cache(tmp_path):
    store=StateStore(tmp_path); service=WeatherService(store,OpenMeteoProvider(Session()))
    assert service.snapshot is None and service.refresh(Settings())
    cached=service.snapshot
    offline=WeatherService(store,OpenMeteoProvider(Session(error=requests.Timeout())))
    assert offline.snapshot == cached
    assert offline.refresh(Settings()) is False
    assert offline.snapshot == cached and offline.last_error

def test_startup_without_cache_survives_failure(tmp_path):
    service=WeatherService(StateStore(tmp_path),OpenMeteoProvider(Session(error=requests.Timeout())))
    assert service.refresh(Settings()) is False and service.snapshot is None

def test_location_search_shape():
    body={"results":[{"name":"Rincon Hill","admin1":"California","country":"United States",
                      "latitude":1,"longitude":2,"timezone":"America/Los_Angeles"}]}
    result=OpenMeteoProvider(Session(Response(body))).search_locations("rincon")
    assert result == [{"label":"Rincon Hill, California, United States","latitude":1,
                       "longitude":2,"timezone":"America/Los_Angeles"}]
