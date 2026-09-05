from __future__ import annotations

import logging
import os
import socket
import threading
import time
from datetime import datetime, timezone
from typing import Callable, Iterable


LOG = logging.getLogger("weather-display")
HEARTBEAT_INTERVAL_SECONDS = 30.0
WORKER_STALL_SECONDS = 120.0


class WorkerHeartbeat:
    """Small, independent liveness record for one background worker."""

    def __init__(self, name: str, stall_after: float = WORKER_STALL_SECONDS):
        self.name = name
        self.stall_after = stall_after
        self._lock = threading.Lock()
        self._monotonic_at = time.monotonic()
        self._wall_at = datetime.now(timezone.utc).isoformat()
        self._state = "starting"

    def progress(self, state: str, *, monotonic_at: float | None = None,
                 wall_at: datetime | None = None) -> None:
        with self._lock:
            self._monotonic_at = time.monotonic() if monotonic_at is None else monotonic_at
            wall_at = wall_at or datetime.now(timezone.utc)
            self._wall_at = wall_at.astimezone(timezone.utc).isoformat()
            self._state = state

    def snapshot(self, monotonic_at: float | None = None) -> dict:
        now = time.monotonic() if monotonic_at is None else monotonic_at
        with self._lock:
            age = max(0.0, now - self._monotonic_at)
            return {
                "state": self._state,
                "last_progress_at": self._wall_at,
                "progress_age_seconds": round(age, 1),
                "stale": age > self.stall_after,
            }


def monitored_wait(wake_event: threading.Event, stop_event: threading.Event,
                   seconds: float, heartbeat: WorkerHeartbeat,
                   interval: float = HEARTBEAT_INTERVAL_SECONDS) -> bool:
    """Wait in bounded pieces, returning true when explicitly woken or stopped."""
    deadline = time.monotonic() + max(0.0, seconds)
    while not stop_event.is_set():
        heartbeat.progress("waiting")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        if wake_event.wait(min(interval, remaining)):
            return True
    return True


def worker_health(heartbeats: Iterable[WorkerHeartbeat]) -> dict[str, dict]:
    return {heartbeat.name: heartbeat.snapshot() for heartbeat in heartbeats}


def notify_systemd(message: str) -> bool:
    """Send an sd_notify datagram without requiring python-systemd."""
    address = os.environ.get("NOTIFY_SOCKET")
    if not address:
        return False
    if address.startswith("@"):
        address = "\0" + address[1:]
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
            sock.sendto(message.encode(), address)
        return True
    except OSError as exc:
        LOG.warning("Could not notify systemd: %s", exc)
        return False


def supervise_workers(heartbeats: Iterable[WorkerHeartbeat], stop_event: threading.Event,
                      terminate: Callable[[int], object] = os._exit,
                      interval: float = 10.0) -> None:
    """Terminate if any worker stops making progress."""
    heartbeats = tuple(heartbeats)
    while not stop_event.wait(interval):
        health = worker_health(heartbeats)
        stalled = [name for name, value in health.items() if value["stale"]]
        if stalled:
            LOG.critical("Worker watchdog detected no progress from: %s", ", ".join(stalled))
            terminate(1)
            return
