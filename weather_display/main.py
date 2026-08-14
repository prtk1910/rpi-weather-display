from __future__ import annotations

import logging
import os
import signal
import threading
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import pygame

from .renderer import DashboardRenderer, HEIGHT, WIDTH
from .state import StateStore
from .weather import OpenMeteoProvider, WeatherService
from .web import create_app


LOG = logging.getLogger("weather-display")


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
    refresh_event, stop_event = threading.Event(), threading.Event()
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
    app = create_app(store, weather, provider, pin, refresh_event,
                     display_status=lambda: display_state["value"])
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
    renderer = DashboardRenderer(screen)
    clock = pygame.time.Clock()
    running, last_key = True, None

    def stop(*_):
        nonlocal running
        running = False
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    display_state["value"] = "rendering"
    try:
        while running:
            for event in pygame.event.get():
                if event.type == pygame.QUIT or (event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE):
                    running = False
            now = datetime.now(timezone.utc)
            settings = store.load_settings()
            snapshot_key = tuple(asdict(weather.snapshot).values()) if weather.snapshot else None
            key = (now.strftime("%Y-%m-%dT%H:%M"), settings, snapshot_key, weather.last_error)
            if key != last_key:
                renderer.render(settings, weather.snapshot, now, weather.last_error)
                pygame.display.flip()
                last_key = key
            clock.tick(5)
    finally:
        display_state["value"] = "stopped"
        stop_event.set(); refresh_event.set()
        pygame.quit()


if __name__ == "__main__": main()
