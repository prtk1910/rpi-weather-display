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
