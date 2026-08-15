from __future__ import annotations

import logging
import os
import signal
import subprocess
import threading
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import pygame

from .renderer import DashboardRenderer, HEIGHT, SCENE_BUTTON_RECT, WIDTH
from .events import EVENT_REFRESH_SECONDS, EventService, FuncheapProvider
from .state import StateStore
from .weather import OpenMeteoProvider, WeatherService
from .web import create_app


LOG = logging.getLogger("weather-display")
SCREEN_BLANKING_REFRESH_SECONDS = 5 * 60


class SceneRotation:
    def __init__(self, started_at: float = 0.0):
        self.started_at = started_at

    def reset(self, now: float) -> None:
        self.started_at = now

    def show_events(self, now: float, weather_seconds: int) -> None:
        self.started_at = now - weather_seconds

    def scene(self, now: float, weather_seconds: int, events_seconds: int) -> str:
        elapsed = max(0.0, now - self.started_at) % (weather_seconds + events_seconds)
        return "weather" if elapsed < weather_seconds else "events"


def toggle_scene(rotation: SceneRotation, now: float, weather_seconds: int,
                 events_seconds: int, events_ready: bool) -> bool:
    if not events_ready:
        return False
    if rotation.scene(now, weather_seconds, events_seconds) == "weather":
        rotation.show_events(now, weather_seconds)
    else:
        rotation.reset(now)
    return True


def disable_screen_blanking(runner=subprocess.run, display: str | None = None) -> bool:
    """Disable X11 blanking after the display connection is known to be ready."""
    display = display or os.environ.get("DISPLAY", ":0")
    commands = (["xset", "-display", display, "s", "off"],
                ["xset", "-display", display, "s", "noblank"],
                ["xset", "-display", display, "-dpms"])
    try:
        results = [runner(command, check=False, stdout=subprocess.DEVNULL,
                          stderr=subprocess.PIPE, text=True) for command in commands]
    except OSError as exc:
        LOG.warning("Could not disable X11 screen blanking: %s", exc)
        return False
    failed = [result.stderr.strip() for result in results if result.returncode]
    if failed:
        LOG.warning("Could not disable X11 screen blanking: %s", failed[0])
        return False
    return True


def main() -> None:
    logging.basicConfig(level=os.environ.get("WEATHER_DISPLAY_LOG_LEVEL", "INFO"),
                        format="%(asctime)s %(levelname)s %(message)s")
    pin = os.environ.get("WEATHER_DISPLAY_PIN", "")
    if not pin:
        raise SystemExit("WEATHER_DISPLAY_PIN must be set")
    if not 4 <= len(pin) <= 64 or "\n" in pin:
        raise SystemExit("WEATHER_DISPLAY_PIN must be 4–64 characters with no newline")
    data_dir = Path(os.environ.get("WEATHER_DISPLAY_DATA_DIR", "/var/lib/weather-display"))
    store = StateStore(data_dir)
    provider = OpenMeteoProvider()
    weather = WeatherService(store, provider)
    events = EventService(store, FuncheapProvider())
    refresh_event = threading.Event()
    cycle_reset_event = threading.Event()
    display_toggle_event = threading.Event()
    events_ready_event = threading.Event()
    stop_event = threading.Event()
    display_state = {"value": "starting"}

    def weather_worker():
        while not stop_event.is_set():
            try:
                weather.refresh(store.load_settings())
            except Exception:
                LOG.exception("Unexpected weather refresh failure")
            refresh_event.wait(600)
            refresh_event.clear()

    worker = threading.Thread(target=weather_worker, name="weather-fetch", daemon=True)
    worker.start()
    def event_worker():
        while not stop_event.is_set():
            delay = events.seconds_until_refresh()
            if delay <= 0:
                try:
                    events.refresh()
                except Exception:
                    LOG.exception("Unexpected event refresh failure")
                delay = EVENT_REFRESH_SECONDS
                cycle_reset_event.set()
            events_ready_event.set()
            stop_event.wait(max(1, delay))

    event_thread = threading.Thread(target=event_worker, name="event-fetch", daemon=True)
    event_thread.start()
    app = create_app(store, weather, provider, pin, refresh_event,
                     display_status=lambda: display_state["value"],
                     cycle_reset_event=cycle_reset_event, events=events,
                     display_toggle_event=display_toggle_event)
    if os.environ.get("WEATHER_DISPLAY_COOKIE_SECURE") == "1":
        app.config["SESSION_COOKIE_SECURE"] = True
    web_thread = threading.Thread(
        target=lambda: app.run(host="0.0.0.0", port=8080, threaded=True, use_reloader=False),
        name="settings-web", daemon=True)
    web_thread.start()

    pygame.init()
    flags = 0 if os.environ.get("WEATHER_DISPLAY_WINDOWED") == "1" else pygame.FULLSCREEN
    screen = pygame.display.set_mode((WIDTH, HEIGHT), flags)
    pygame.display.set_caption("Weather Display")
    pygame.mouse.set_visible(False)
    x11_display = pygame.display.get_driver() == "x11"
    if x11_display:
        disable_screen_blanking()
    frame = pygame.Surface((WIDTH, HEIGHT))
    renderer = DashboardRenderer(frame)
    clock = pygame.time.Clock()
    running, last_key = True, None
    rotation = SceneRotation(time.monotonic())
    blanking_checked_at = time.monotonic()

    def stop(*_):
        nonlocal running
        running = False
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    display_state["value"] = "rendering"
    try:
        while running:
            touch_toggle_requested = False
            for event in pygame.event.get():
                if event.type == pygame.QUIT or (event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE):
                    running = False
                elif event.type == pygame.MOUSEBUTTONUP and SCENE_BUTTON_RECT.collidepoint(event.pos):
                    touch_toggle_requested = True
                elif event.type == pygame.FINGERUP:
                    point = (round(event.x * WIDTH), round(event.y * HEIGHT))
                    if SCENE_BUTTON_RECT.collidepoint(point):
                        touch_toggle_requested = True
            now = datetime.now(timezone.utc)
            settings = store.load_settings()
            monotonic_now = time.monotonic()
            if x11_display and monotonic_now - blanking_checked_at >= SCREEN_BLANKING_REFRESH_SECONDS:
                disable_screen_blanking()
                blanking_checked_at = monotonic_now
            if cycle_reset_event.is_set():
                cycle_reset_event.clear()
                rotation.reset(monotonic_now)
            if touch_toggle_requested:
                display_toggle_event.set()
            if display_toggle_event.is_set() and toggle_scene(
                    rotation, monotonic_now, settings.weather_scene_seconds,
                    settings.events_scene_seconds, events_ready_event.is_set()):
                display_toggle_event.clear()
            if events_ready_event.is_set():
                scene = rotation.scene(monotonic_now, settings.weather_scene_seconds,
                                       settings.events_scene_seconds)
            else:
                rotation.reset(monotonic_now)
                scene = "weather"
            snapshot_key = tuple(asdict(weather.snapshot).values()) if weather.snapshot else None
            selection = events.selection(settings, now) if scene == "events" else None
            key = (scene, now.strftime("%Y-%m-%dT%H:%M"), settings, snapshot_key,
                   weather.last_error, selection, events.last_fetched_at, events.last_error)
            if key != last_key:
                if scene == "weather":
                    renderer.render(settings, weather.snapshot, now, weather.last_error)
                else:
                    renderer.render_events(settings, selection, now, events.last_fetched_at,
                                           events.last_error)
                screen.blit(frame, (0, 0))
                pygame.display.flip()
                last_key = key
            clock.tick(5)
    finally:
        display_state["value"] = "stopped"
        stop_event.set(); refresh_event.set()
        pygame.quit()


if __name__ == "__main__": main()
