from __future__ import annotations

import hmac
import secrets
import threading
import time
from collections import defaultdict, deque
from dataclasses import asdict
from datetime import datetime, timezone
from functools import wraps
from urllib.parse import urlsplit

from flask import Flask, abort, jsonify, redirect, render_template, request, session, url_for

from .state import EVENT_CATEGORIES, Settings, StateStore
from .events import EventService
from .weather import OpenMeteoProvider, WeatherError, WeatherService


class LoginThrottle:
    def __init__(self, limit=5, window=300):
        self.limit, self.window = limit, window
        self.failures: dict[str, deque[float]] = defaultdict(deque)
        self.lock = threading.Lock()

    def blocked(self, address: str) -> bool:
        now = time.monotonic()
        with self.lock:
            items = self.failures[address]
            while items and items[0] < now - self.window: items.popleft()
            return len(items) >= self.limit

    def fail(self, address: str) -> None:
        with self.lock: self.failures[address].append(time.monotonic())

    def clear(self, address: str) -> None:
        with self.lock: self.failures.pop(address, None)


def create_app(store: StateStore, weather: WeatherService, provider: OpenMeteoProvider,
               pin: str, refresh_event: threading.Event | None = None,
               display_status=lambda: "starting",
               cycle_reset_event: threading.Event | None = None,
               events: EventService | None = None) -> Flask:
    if not pin:
        raise RuntimeError("WEATHER_DISPLAY_PIN is required")
    app = Flask(__name__)
    app.secret_key = store.session_secret()
    app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
                      SESSION_COOKIE_SECURE=False, PERMANENT_SESSION_LIFETIME=86400)
    throttle = LoginThrottle()
    refresh_event = refresh_event or threading.Event()
    cycle_reset_event = cycle_reset_event or threading.Event()

    def authenticated(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            if not session.get("authenticated"):
                if request.path.startswith("/api/"): return jsonify(error="authentication required"), 401
                return redirect(url_for("login"))
            return fn(*args, **kwargs)
        return wrapped

    def same_origin() -> bool:
        origin = request.headers.get("Origin")
        if origin and urlsplit(origin).netloc != request.host: return False
        token = request.headers.get("X-CSRF-Token")
        return bool(token and hmac.compare_digest(token, session.get("csrf", "")))

    @app.get("/")
    def index(): return redirect(url_for("settings_page" if session.get("authenticated") else "login"))

    @app.route("/login", methods=["GET", "POST"])
    def login():
        address = request.remote_addr or "unknown"
        error = None
        if request.method == "POST":
            if throttle.blocked(address):
                return render_template("login.html", error="Too many attempts. Try again in a few minutes."), 429
            supplied = request.form.get("pin", "")
            if hmac.compare_digest(supplied.encode(), pin.encode()):
                throttle.clear(address); session.clear(); session["authenticated"] = True
                session["csrf"] = secrets.token_urlsafe(24); session.permanent = True
                return redirect(url_for("settings_page"))
            throttle.fail(address); error = "Incorrect PIN"
        return render_template("login.html", error=error)

    @app.post("/logout")
    @authenticated
    def logout():
        if not same_origin(): abort(403)
        session.clear(); return redirect(url_for("login"))

    @app.get("/settings")
    @authenticated
    def settings_page():
        return render_template("settings.html", settings=store.load_settings(), csrf=session["csrf"],
                               event_categories=EVENT_CATEGORIES)

    @app.get("/api/settings")
    @authenticated
    def get_settings(): return jsonify(asdict(store.load_settings()))

    @app.put("/api/settings")
    @authenticated
    def put_settings():
        if not same_origin(): abort(403)
        try:
            candidate = Settings.from_dict(request.get_json(force=False, silent=False))
            store.save_settings(candidate)
        except (ValueError, TypeError) as exc:
            return jsonify(error=str(exc)), 400
        refresh_event.set()
        cycle_reset_event.set()
        return jsonify(asdict(candidate))

    @app.get("/api/locations")
    @authenticated
    def locations():
        query = request.args.get("q", "").strip()
        if len(query) < 2: return jsonify([])
        try: return jsonify(provider.search_locations(query))
        except WeatherError as exc: return jsonify(error=str(exc)), 502

    @app.get("/healthz")
    def health():
        snapshot = weather.snapshot
        age = None
        if snapshot:
            fetched = datetime.fromisoformat(snapshot.fetched_at.replace("Z", "+00:00"))
            age = max(0, int((datetime.now(timezone.utc) - fetched).total_seconds()))
        return jsonify(service="ok", weather_cache_age_seconds=age,
                       weather_error=weather.last_error,
                       event_cache_age_seconds=events.cache_age_seconds() if events else None,
                       event_error=events.last_error if events else None,
                       display=display_status())

    return app
