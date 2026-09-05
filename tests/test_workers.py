import threading
import time
from datetime import datetime, timezone

from weather_display.workers import WorkerHeartbeat, supervise_workers


def test_worker_heartbeat_reports_state_age_and_staleness():
    heartbeat = WorkerHeartbeat("weather", stall_after=120)
    at = datetime(2026, 8, 14, 19, 0, tzinfo=timezone.utc)
    heartbeat.progress("fetching", monotonic_at=10, wall_at=at)
    fresh = heartbeat.snapshot(monotonic_at=15)
    assert fresh == {
        "state": "fetching", "last_progress_at": at.isoformat(),
        "progress_age_seconds": 5, "stale": False,
    }
    assert heartbeat.snapshot(monotonic_at=131)["stale"] is True


def test_supervisor_terminates_process_when_worker_stalls():
    heartbeat = WorkerHeartbeat("events", stall_after=1)
    heartbeat.progress("waiting", monotonic_at=time.monotonic() - 2)
    exits = []
    supervise_workers((heartbeat,), threading.Event(), terminate=exits.append, interval=0.001)
    assert exits == [1]
