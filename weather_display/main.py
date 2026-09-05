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
from .events import EVENT_REFRESH_SECONDS, EventService, FuncheapProvider, event_wait_seconds
from .state import StateStore
from .weather import OpenMeteoProvider, WeatherService
from .web import create_app
from .workers import (WorkerHeartbeat, monitored_wait, notify_systemd, supervise_workers,
                      worker_health)


LOG = logging.getLogger("weather-display")
DISPLAY_CONFIRM_SECONDS = 0.5
WEATHER_REFRESH_SECONDS = 10 * 60
EVENT_RETRY_SECONDS = 5 * 60
SYSTEMD_WATCHDOG_INTERVAL_SECONDS = 30
DISPLAY_REPAINT_EVENTS = frozenset(
    event_type for name in (
        "WINDOWEXPOSED", "WINDOWSHOWN", "WINDOWRESTORED", "WINDOWFOCUSGAINED",
        "WINDOWMAXIMIZED", "WINDOWRESIZED", "WINDOWSIZECHANGED",
    )
    if (event_type := getattr(pygame, name, None)) is not None
)


def display_event_needs_repaint(event_type: int) -> bool:
    """Return whether X11 may need the saved frame presented again."""
    return event_type in DISPLAY_REPAINT_EVENTS


class FrameConfirmation:
    """Request one follow-up present after an asynchronous SPI scene change."""

    def __init__(self, delay: float = DISPLAY_CONFIRM_SECONDS):
        self.delay = delay
        self.deadline: float | None = None

    def schedule(self, now: float) -> None:
        self.deadline = now + self.delay

    def take_if_due(self, now: float) -> bool:
        if self.deadline is None or now < self.deadline:
            return False
        self.deadline = None
        return True


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
    weather_heartbeat = WorkerHeartbeat("weather")
    event_heartbeat = WorkerHeartbeat("events")
    heartbeats = (weather_heartbeat, event_heartbeat)

    def weather_worker():
        while not stop_event.is_set():
            weather_heartbeat.progress("fetching")
            try:
                weather.refresh(store.load_settings())
            except Exception:
                LOG.exception("Unexpected weather refresh failure")
            else:
                if weather.last_error:
                    LOG.warning("Weather refresh failed: %s", weather.last_error)
                else:
                    LOG.info("Weather refresh complete")
            monitored_wait(refresh_event, stop_event, WEATHER_REFRESH_SECONDS,
                           weather_heartbeat)
            refresh_event.clear()

    worker = threading.Thread(target=weather_worker, name="weather-fetch", daemon=True)
    worker.start()
    def event_worker():
        while not stop_event.is_set():
            event_heartbeat.progress("scheduling")
            now = datetime.now(timezone.utc)
            try:
                delay = events.seconds_until_refresh(now)
            except Exception:
                LOG.exception("Unexpected event scheduling failure")
                delay = EVENT_RETRY_SECONDS
            if delay <= 0:
                event_heartbeat.progress("fetching")
                try:
                    changed = events.refresh(now)
                except Exception:
                    LOG.exception("Unexpected event refresh failure")
                    delay = EVENT_RETRY_SECONDS
                else:
                    if events.last_error:
                        LOG.warning("Event refresh incomplete: %s", events.last_error)
                        delay = EVENT_RETRY_SECONDS
                    else:
                        LOG.info("Event refresh complete%s", " with changes" if changed else "")
                        delay = EVENT_REFRESH_SECONDS
                cycle_reset_event.set()
            events_ready_event.set()
            # A fresh six-hour cache must never carry the worker past a date rollover.
            delay = event_wait_seconds(delay, now)
            monitored_wait(stop_event, stop_event, delay, event_heartbeat)

    event_thread = threading.Thread(target=event_worker, name="event-fetch", daemon=True)
    event_thread.start()
    supervisor_thread = threading.Thread(
        target=lambda: supervise_workers(heartbeats, stop_event),
        name="worker-watchdog", daemon=True)
    supervisor_thread.start()
    app = create_app(store, weather, provider, pin, refresh_event,
                     display_status=lambda: display_state["value"],
                     cycle_reset_event=cycle_reset_event, events=events,
                     display_toggle_event=display_toggle_event,
                     worker_status=lambda: worker_health(heartbeats))
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
    frame_confirmation = FrameConfirmation()
    last_loop_at = time.perf_counter()

    def stop(*_):
        nonlocal running
        running = False
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    display_state["value"] = "rendering"
    notify_systemd("READY=1\nSTATUS=Rendering weather dashboard")
    watchdog_notified_at = time.monotonic()
    try:
        while running:
            loop_at = time.perf_counter()
            loop_gap_ms = (loop_at - last_loop_at) * 1000
            if loop_gap_ms > 1000:
                LOG.warning("Display loop stalled for %.1f ms", loop_gap_ms)
            last_loop_at = loop_at
            touch_toggle_requested = False
            repaint_reason = None
            for event in pygame.event.get():
                event_name = pygame.event.event_name(event.type)
                if event_name.startswith("Window"):
                    LOG.info("Display event: %s", event_name)
                if display_event_needs_repaint(event.type):
                    repaint_reason = event_name
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
            if monotonic_now - watchdog_notified_at >= SYSTEMD_WATCHDOG_INTERVAL_SECONDS:
                notify_systemd("WATCHDOG=1")
                watchdog_notified_at = monotonic_now
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
            prepare_started = time.perf_counter()
            selection = events.selection(settings, now) if scene == "events" else None
            prepare_ms = (time.perf_counter() - prepare_started) * 1000
            key = (scene, now.strftime("%Y-%m-%dT%H:%M"), settings, snapshot_key,
                   weather.last_error, selection, events.last_fetched_at, events.last_error)
            content_changed = key != last_key
            if content_changed:
                scene_changed = last_key is None or scene != last_key[0]
                render_started = time.perf_counter()
                if scene == "weather":
                    renderer.render(settings, weather.snapshot, now, weather.last_error)
                else:
                    renderer.render_events(settings, selection, now, events.last_fetched_at,
                                           events.last_error)
                render_ms = (time.perf_counter() - render_started) * 1000
                last_key = key
            else:
                scene_changed = False
                render_ms = 0.0
            confirmation_repaint = frame_confirmation.take_if_due(monotonic_now)
            if content_changed or repaint_reason or confirmation_repaint:
                screen.blit(frame, (0, 0))
                flip_started = time.perf_counter()
                pygame.display.flip()
                flip_ms = (time.perf_counter() - flip_started) * 1000
                if scene_changed:
                    frame_confirmation.schedule(monotonic_now)
                if scene_changed or prepare_ms > 250 or render_ms > 250 or flip_ms > 250:
                    LOG.info("Displayed %s scene: prepare=%.1f ms render=%.1f ms flip=%.1f ms",
                             scene, prepare_ms, render_ms, flip_ms)
                elif repaint_reason:
                    LOG.info("Repainted %s scene after %s: flip=%.1f ms",
                             scene, repaint_reason, flip_ms)
                elif confirmation_repaint:
                    LOG.info("Confirmed %s scene: flip=%.1f ms", scene, flip_ms)
            clock.tick(5)
    finally:
        display_state["value"] = "stopped"
        stop_event.set(); refresh_event.set()
        pygame.quit()


if __name__ == "__main__": main()
