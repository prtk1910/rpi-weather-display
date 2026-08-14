import json
from dataclasses import replace

import pytest

from weather_display.state import Settings, StateStore


def test_defaults_and_round_trip(tmp_path):
    store = StateStore(tmp_path)
    assert store.load_settings().location_label == "Rincon Hill"
    changed = replace(Settings(), units="imperial", clock_format="12h")
    store.save_settings(changed)
    assert store.load_settings() == changed
    assert oct(store.settings_path.stat().st_mode & 0o777) == "0o600"

@pytest.mark.parametrize("change", [
    {"latitude": 91}, {"longitude": -181}, {"units": "kelvin"},
    {"clock_format": "analog"}, {"timezone": "Mars/Olympus"}, {"location_label": ""},
])
def test_validation(change):
    value = Settings().__dict__ | change
    with pytest.raises(ValueError): Settings.from_dict(value)

def test_rejects_unknown_keys():
    with pytest.raises(ValueError): Settings.from_dict(Settings().__dict__ | {"pin": "secret"})

def test_atomic_write_leaves_valid_original_on_replace_failure(tmp_path, monkeypatch):
    store = StateStore(tmp_path); store.save_settings(Settings())
    import weather_display.state as state
    monkeypatch.setattr(state.os, "replace", lambda *_: (_ for _ in ()).throw(OSError("disk")))
    with pytest.raises(OSError): store.save_settings(replace(Settings(), units="imperial"))
    assert json.loads(store.settings_path.read_text())["units"] == "metric"

def test_cache_tolerates_corruption(tmp_path):
    store = StateStore(tmp_path); store.cache_path.write_text("nope")
    assert store.load_cache() is None

def test_old_settings_file_gets_new_defaults(tmp_path):
    store = StateStore(tmp_path)
    store.settings_path.write_text(json.dumps({
        "location_label": "Mission", "latitude": 37.76, "longitude": -122.42,
        "timezone": "America/Los_Angeles", "units": "metric", "clock_format": "24h",
    }))
    settings = store.load_settings()
    assert settings.weather_scene_seconds == 20
    assert settings.events_scene_seconds == 10
    assert settings.event_categories == ("Top Pick", "Art & Museums", "Fairs & Festivals", "Eating & Drinking")

@pytest.mark.parametrize("field,value", [
    ("weather_scene_seconds", 4), ("weather_scene_seconds", 301),
    ("events_scene_seconds", 4), ("events_scene_seconds", 301),
])
def test_duration_validation(field, value):
    with pytest.raises(ValueError, match="between 5 and 300"):
        Settings.from_dict(Settings().__dict__ | {field: value})

def test_event_categories_are_exact_and_oriented_as_a_list():
    value = Settings.from_dict(Settings().__dict__ | {"event_categories": ["Comedy", "Live Music"]})
    assert value.event_categories == ("Comedy", "Live Music")
    with pytest.raises(ValueError, match="unknown event category"):
        Settings.from_dict(Settings().__dict__ | {"event_categories": ["music"]})
