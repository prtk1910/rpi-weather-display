from __future__ import annotations

import json
import os
import secrets
import tempfile
import threading
from dataclasses import asdict, dataclass
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


EVENT_CATEGORIES = (
    "Top Pick", "Art & Museums", "Charity & Volunteering", "Club / DJ",
    "Comedy", "Eating & Drinking", "Fairs & Festivals", "Free Stuff",
    "Fun & Games", "Geek Event", "Kids & Families", "Lectures & Workshops",
    "Literature", "Live Music", "Movies", "Shopping & Fashion",
    "Sports & Fitness", "Theater & Performance",
)
DEFAULT_EVENT_CATEGORIES = ("Top Pick", "Art & Museums", "Fairs & Festivals", "Eating & Drinking")


def _duration(value: object) -> int:
    if isinstance(value, bool):
        raise ValueError("display times must be whole numbers")
    result = int(value)
    if float(value) != result:
        raise ValueError("display times must be whole numbers")
    return result


@dataclass(frozen=True)
class Settings:
    location_label: str = "Rincon Hill"
    latitude: float = 37.78521
    longitude: float = -122.39192
    timezone: str = "America/Los_Angeles"
    units: str = "metric"
    clock_format: str = "24h"
    weather_scene_seconds: int = 20
    events_scene_seconds: int = 10
    event_categories: tuple[str, ...] = DEFAULT_EVENT_CATEGORIES

    @classmethod
    def from_dict(cls, value: dict) -> "Settings":
        if not isinstance(value, dict):
            raise ValueError("settings must be an object")
        expected = {f.name for f in cls.__dataclass_fields__.values()}
        unknown = set(value) - expected
        if unknown:
            raise ValueError(f"unknown setting: {sorted(unknown)[0]}")
        try:
            categories = value.get("event_categories", cls.event_categories)
            if not isinstance(categories, (list, tuple)) or isinstance(categories, (str, bytes)):
                raise ValueError("event_categories must be a list")
            result = cls(
                location_label=str(value.get("location_label", cls.location_label)).strip(),
                latitude=float(value.get("latitude", cls.latitude)),
                longitude=float(value.get("longitude", cls.longitude)),
                timezone=str(value.get("timezone", cls.timezone)).strip(),
                units=str(value.get("units", cls.units)),
                clock_format=str(value.get("clock_format", cls.clock_format)),
                weather_scene_seconds=_duration(value.get("weather_scene_seconds", cls.weather_scene_seconds)),
                events_scene_seconds=_duration(value.get("events_scene_seconds", cls.events_scene_seconds)),
                event_categories=tuple(str(item) for item in categories),
            )
        except (TypeError, ValueError) as exc:
            raise ValueError(str(exc) or "settings contain invalid values") from exc
        result.validate()
        return result

    def validate(self) -> None:
        if not self.location_label or len(self.location_label) > 80:
            raise ValueError("location label must be 1–80 characters")
        if not -90 <= self.latitude <= 90 or not -180 <= self.longitude <= 180:
            raise ValueError("coordinates are outside their valid range")
        if self.units not in ("metric", "imperial"):
            raise ValueError("units must be metric or imperial")
        if self.clock_format not in ("12h", "24h"):
            raise ValueError("clock_format must be 12h or 24h")
        if not 5 <= self.weather_scene_seconds <= 300 or not 5 <= self.events_scene_seconds <= 300:
            raise ValueError("display times must be between 5 and 300 seconds")
        if len(set(self.event_categories)) != len(self.event_categories):
            raise ValueError("event_categories must not contain duplicates")
        invalid = [item for item in self.event_categories if item not in EVENT_CATEGORIES]
        if invalid:
            raise ValueError(f"unknown event category: {invalid[0]}")
        try:
            ZoneInfo(self.timezone)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("unknown timezone") from exc


def atomic_write_json(path: Path, value: object, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class StateStore:
    def __init__(self, directory: str | Path):
        self.directory = Path(directory)
        self.settings_path = self.directory / "settings.json"
        self.cache_path = self.directory / "weather-cache.json"
        self.event_cache_dir = self.directory / "event-cache"
        self.secret_path = self.directory / "session-secret"
        self._lock = threading.RLock()

    def load_settings(self) -> Settings:
        with self._lock:
            if not self.settings_path.exists():
                return Settings()
            return Settings.from_dict(json.loads(self.settings_path.read_text(encoding="utf-8")))

    def save_settings(self, settings: Settings) -> None:
        settings.validate()
        with self._lock:
            atomic_write_json(self.settings_path, asdict(settings))

    def load_cache(self) -> dict | None:
        with self._lock:
            try:
                value = json.loads(self.cache_path.read_text(encoding="utf-8"))
                return value if isinstance(value, dict) else None
            except (FileNotFoundError, json.JSONDecodeError, OSError):
                return None

    def save_cache(self, value: dict) -> None:
        with self._lock:
            atomic_write_json(self.cache_path, value)

    def load_event_cache(self, date_key: str) -> dict | None:
        path = self.event_cache_dir / f"{date_key}.json"
        with self._lock:
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
                return value if isinstance(value, dict) else None
            except (FileNotFoundError, json.JSONDecodeError, OSError):
                return None

    def save_event_cache(self, date_key: str, value: dict) -> None:
        with self._lock:
            atomic_write_json(self.event_cache_dir / f"{date_key}.json", value)

    def session_secret(self) -> str:
        with self._lock:
            try:
                return self.secret_path.read_text(encoding="ascii").strip()
            except FileNotFoundError:
                self.directory.mkdir(parents=True, exist_ok=True)
                secret = secrets.token_hex(32)
                fd = os.open(self.secret_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "w", encoding="ascii") as handle:
                    handle.write(secret)
                return secret
