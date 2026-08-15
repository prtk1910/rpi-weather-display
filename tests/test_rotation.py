from types import SimpleNamespace

from weather_display.main import SceneRotation, disable_screen_blanking


def test_rotation_uses_independent_durations_and_boundaries():
    rotation = SceneRotation(100.0)
    assert rotation.scene(100.0, 20, 10) == "weather"
    assert rotation.scene(119.999, 20, 10) == "weather"
    assert rotation.scene(120.0, 20, 10) == "events"
    assert rotation.scene(129.999, 20, 10) == "events"
    assert rotation.scene(130.0, 20, 10) == "weather"
    assert rotation.scene(145.0, 5, 25) == "events"


def test_reset_immediately_restarts_on_weather():
    rotation = SceneRotation(0.0)
    assert rotation.scene(25.0, 20, 10) == "events"
    rotation.reset(25.0)
    assert rotation.scene(25.0, 20, 10) == "weather"


def test_manual_switch_shows_events_for_the_full_configured_duration():
    rotation = SceneRotation(100.0)
    rotation.show_events(105.0, 20)
    assert rotation.scene(105.0, 20, 10) == "events"
    assert rotation.scene(114.999, 20, 10) == "events"
    assert rotation.scene(115.0, 20, 10) == "weather"


def test_screen_blanking_is_disabled_after_x11_is_ready():
    calls = []
    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0, stderr="")
    assert disable_screen_blanking(run, ":7") is True
    assert [call[0] for call in calls] == [
        ["xset", "-display", ":7", "s", "off"],
        ["xset", "-display", ":7", "s", "noblank"],
        ["xset", "-display", ":7", "-dpms"],
    ]
