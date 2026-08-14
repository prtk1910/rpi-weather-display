from weather_display.main import SceneRotation


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
