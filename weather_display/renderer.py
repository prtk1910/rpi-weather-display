from __future__ import annotations

import math
from datetime import datetime, timezone
from pathlib import Path

import pygame

from .state import Settings
from .util import age_label, compass_direction, condition_for, format_clock, local_datetime, rounded, uv_risk
from .weather import WeatherSnapshot


WIDTH, HEIGHT = 480, 320
TEXT = (239, 244, 247)
MUTED = (181, 194, 204)
THEMES = {
    "default": {"bg": (9, 15, 21), "card": (21, 31, 40), "accent": (99, 190, 226), "warm": (244, 194, 72)},
    "clear": {"bg": (8, 25, 38), "card": (17, 47, 63), "accent": (255, 202, 79), "warm": (255, 202, 79)},
    "night": {"bg": (12, 15, 37), "card": (28, 31, 65), "accent": (174, 190, 255), "warm": (213, 220, 255)},
    "cloud": {"bg": (10, 23, 31), "card": (24, 48, 59), "accent": (121, 207, 238), "warm": (242, 190, 72)},
    "rain": {"bg": (8, 20, 33), "card": (16, 39, 57), "accent": (83, 181, 235), "warm": (244, 190, 72)},
    "fog": {"bg": (22, 28, 31), "card": (39, 48, 52), "accent": (190, 211, 216), "warm": (242, 190, 72)},
    "snow": {"bg": (15, 27, 39), "card": (30, 50, 65), "accent": (205, 235, 247), "warm": (242, 190, 72)},
    "storm": {"bg": (22, 14, 38), "card": (45, 29, 67), "accent": (201, 153, 255), "warm": (255, 201, 69)},
}


class DashboardRenderer:
    def __init__(self, surface: pygame.Surface):
        self.surface = surface
        self.fonts = {size: pygame.font.Font(None, size) for size in (13, 16, 18, 20, 22, 24, 28, 30, 32, 34, 44, 48, 68, 76)}
        for size, font in self.fonts.items():
            font.set_bold(size >= 18)
        self._icon_cache: dict[tuple, pygame.Surface] = {}
        self.theme = THEMES["default"]

    def render(self, settings: Settings, weather: WeatherSnapshot | None,
               now: datetime | None = None, error: str | None = None) -> None:
        now = now or datetime.now(timezone.utc)
        icon_name = condition_for(weather.weather_code, weather.is_day)[1] if weather else "unknown"
        self.theme = _theme_for(icon_name)
        self.surface.fill(self.theme["bg"])
        local = local_datetime(now, settings.timezone)
        self._text(_truncate(settings.location_label, 18), 24, TEXT, (12, 4))
        self._text(local.strftime("%a, %b %-d"), 20, MUTED, (12, 33))
        clock = format_clock(now, settings.timezone, settings.clock_format)
        clock_font = 48 if settings.clock_format == "12h" else 68
        self._text(clock, clock_font, TEXT, (468, 0), anchor="topright")

        if weather is None:
            self._draw_icon("unknown", (40, 82), 96)
            self._text("Waiting for weather", 30, TEXT, (150, 99))
            self._text("Retrying automatically…", 20, MUTED, (150, 139))
            self._draw_cards(None)
        else:
            condition, icon = condition_for(weather.weather_code, weather.is_day)
            self._draw_icon(icon, (36, 80), 98)
            self._text(f"{rounded(weather.temperature)}{weather.temperature_unit}", 76, self.theme["accent"], (148, 51))
            self._text(condition, 32, TEXT, (151, 125))
            secondary = (f"Feels {rounded(weather.apparent_temperature)}°  "
                         f"H {rounded(weather.high)}°  L {rounded(weather.low)}°")
            self._text(secondary, 22, MUTED, (151, 157))
            self._draw_cards(weather)

        fetched = _parse_time(weather.fetched_at) if weather else None
        age = (now - fetched).total_seconds() if fetched else None
        stale = age is not None and age > 15 * 60
        if weather:
            status = f"Updated {age_label(age or 0)}"
            if stale or error: status = "STALE · " + status
        else:
            status = "No cached data · retrying"
        self._text("Weather data by Open-Meteo.com", 13, MUTED, (8, 305))
        self._text(status, 13, (231, 150, 75) if stale or error else MUTED, (472, 305), anchor="topright")

    def _draw_cards(self, weather: WeatherSnapshot | None) -> None:
        cards = ((6, 186, 152, 110), (164, 186, 152, 110), (322, 186, 152, 110))
        for rect in cards:
            pygame.draw.rect(self.surface, self.theme["card"], rect, border_radius=10)
        if not weather:
            self._text("WIND", 20, MUTED, (18, 197))
            self._text("UV NOW", 20, MUTED, (176, 197))
            self._text("RAIN NOW", 20, MUTED, (334, 197))
            for x in (18, 176, 334): self._text("—", 44, TEXT, (x, 220))
            return
        wind = f"{rounded(weather.wind_speed)} {weather.wind_unit}"
        direction = compass_direction(weather.wind_direction)
        self._text(f"WIND · {direction}", 20, MUTED, (18, 197))
        self._text("UV NOW", 20, MUTED, (176, 197))
        self._text("RAIN NOW", 20, MUTED, (334, 197))
        self._text(wind, 34 if len(wind) < 10 else 30, TEXT, (18, 220))
        self._text(f"Gusts {rounded(weather.wind_gusts)} {weather.wind_unit}", 20, MUTED, (18, 264))
        self._text(str(round(weather.uv_index, 1)), 44, self.theme["accent"], (176, 216))
        self._text(uv_risk(weather.uv_index), 20, MUTED, (176, 264))
        self._text(f"{weather.precipitation_probability}%", 44, self.theme["accent"], (334, 216))
        self._text("next hour", 20, MUTED, (334, 264))

    def _text(self, value: str, size: int, color: tuple[int, int, int], pos: tuple[int, int], anchor="topleft"):
        rendered = self.fonts[size].render(str(value), True, color)
        rect = rendered.get_rect()
        setattr(rect, anchor, pos)
        self.surface.blit(rendered, rect)

    def _draw_icon(self, name: str, pos: tuple[int, int], size: int):
        key = (name, size, self.theme["accent"], self.theme["warm"])
        if key not in self._icon_cache:
            self._icon_cache[key] = _make_icon(name, size, self.theme)
        self.surface.blit(self._icon_cache[key], pos)


def _make_icon(name: str, size: int, theme: dict) -> pygame.Surface:
    scale = size / 100
    surf = pygame.Surface((size, size), pygame.SRCALPHA)
    def pt(x, y): return (round(x * scale), round(y * scale))
    if "night" in name:
        pygame.draw.circle(surf, (208, 220, 236), pt(45, 39), round(24 * scale))
        pygame.draw.circle(surf, (0, 0, 0, 0), pt(57, 29), round(22 * scale))
    elif name in ("clear", "partly-cloudy"):
        pygame.draw.circle(surf, theme["warm"], pt(40, 38), round(22 * scale))
        for angle in range(0, 360, 45):
            a = math.radians(angle)
            pygame.draw.line(surf, theme["warm"], pt(40 + 29*math.cos(a), 38 + 29*math.sin(a)),
                             pt(40 + 37*math.cos(a), 38 + 37*math.sin(a)), max(2, round(4*scale)))
    if name in ("cloudy", "partly-cloudy", "partly-cloudy-night", "rain", "snow", "sleet", "storm"):
        cloud = (164, 181, 194)
        pygame.draw.circle(surf, cloud, pt(38, 55), round(20*scale))
        pygame.draw.circle(surf, cloud, pt(57, 47), round(25*scale))
        pygame.draw.circle(surf, cloud, pt(75, 58), round(17*scale))
        pygame.draw.rect(surf, cloud, (*pt(28, 55), *pt(57, 21)), border_radius=round(10*scale))
    if name in ("rain", "sleet", "storm"):
        for x in (38, 56, 74): pygame.draw.line(surf, theme["accent"], pt(x, 78), pt(x-4, 90), max(2, round(4*scale)))
    if name in ("snow", "sleet"):
        for x in (38, 57, 76): pygame.draw.circle(surf, TEXT, pt(x, 86), max(2, round(3*scale)))
    if name == "storm":
        pygame.draw.polygon(surf, theme["warm"], [pt(58, 72), pt(45, 91), pt(57, 88), pt(49, 99), pt(70, 81), pt(58, 83)])
    if name == "fog":
        for y, width in ((35, 62), (50, 72), (65, 58), (80, 68)):
            pygame.draw.line(surf, (170, 186, 196), pt(14, y), pt(14+width, y), max(2, round(5*scale)))
    if name == "unknown":
        pygame.draw.circle(surf, MUTED, pt(50, 50), round(31*scale), max(2, round(4*scale)))
        font = pygame.font.Font(None, round(52*scale)); mark = font.render("?", True, MUTED)
        surf.blit(mark, mark.get_rect(center=pt(50, 52)))
    return surf


def _theme_for(icon: str) -> dict:
    if "night" in icon: return THEMES["night"]
    if icon == "clear": return THEMES["clear"]
    if icon in ("cloudy", "partly-cloudy"): return THEMES["cloud"]
    if icon in ("rain", "sleet"): return THEMES["rain"]
    if icon == "fog": return THEMES["fog"]
    if icon == "snow": return THEMES["snow"]
    if icon == "storm": return THEMES["storm"]
    return THEMES["default"]


def _truncate(value: str, count: int) -> str:
    return value if len(value) <= count else value[:count-1].rstrip() + "…"


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def save_preview(path: str | Path, settings: Settings, weather: WeatherSnapshot | None,
                 now: datetime, error: str | None = None) -> None:
    pygame.init()
    surface = pygame.Surface((WIDTH, HEIGHT))
    DashboardRenderer(surface).render(settings, weather, now, error)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    pygame.image.save(surface, str(path))
    pygame.quit()
