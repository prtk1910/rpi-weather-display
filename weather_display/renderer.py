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
MUTED = (166, 180, 191)
THEMES = {
    "default": {"bg": (9, 15, 21), "card": (21, 31, 40), "accent": (99, 190, 226), "warm": (244, 194, 72)},
    "clear": {"bg": (8, 25, 38), "card": (17, 47, 63), "accent": (255, 202, 79), "warm": (255, 202, 79)},
    "night": {"bg": (12, 15, 37), "card": (28, 31, 65), "accent": (174, 190, 255), "warm": (213, 220, 255)},
    "cloud": {"bg": (13, 23, 30), "card": (25, 42, 50), "accent": (168, 203, 218), "warm": (242, 190, 72)},
    "rain": {"bg": (8, 20, 33), "card": (16, 39, 57), "accent": (83, 181, 235), "warm": (244, 190, 72)},
    "fog": {"bg": (22, 28, 31), "card": (39, 48, 52), "accent": (190, 211, 216), "warm": (242, 190, 72)},
    "snow": {"bg": (15, 27, 39), "card": (30, 50, 65), "accent": (205, 235, 247), "warm": (242, 190, 72)},
    "storm": {"bg": (22, 14, 38), "card": (45, 29, 67), "accent": (201, 153, 255), "warm": (255, 201, 69)},
}


class DashboardRenderer:
    def __init__(self, surface: pygame.Surface):
        self.surface = surface
        self.fonts = {size: pygame.font.Font(None, size) for size in (13, 15, 16, 18, 20, 24, 26, 28, 34, 48, 58, 64)}
        self._icon_cache: dict[tuple, pygame.Surface] = {}
        self.theme = THEMES["default"]

    def render(self, settings: Settings, weather: WeatherSnapshot | None,
               now: datetime | None = None, error: str | None = None) -> None:
        now = now or datetime.now(timezone.utc)
        icon_name = condition_for(weather.weather_code, weather.is_day)[1] if weather else "unknown"
        self.theme = _theme_for(icon_name)
        self.surface.fill(self.theme["bg"])
        local = local_datetime(now, settings.timezone)
        self._text(_truncate(settings.location_label, 24), 20, TEXT, (12, 7))
        self._text(local.strftime("%a, %b %-d"), 16, MUTED, (12, 31))
        clock = format_clock(now, settings.timezone, settings.clock_format)
        clock_font = 48 if settings.clock_format == "12h" else 58
        self._text(clock, clock_font, TEXT, (468, 3), anchor="topright")

        if weather is None:
            self._draw_icon("unknown", (68, 116), 68)
            self._text("Waiting for weather", 26, TEXT, (150, 105))
            self._text("Retrying automatically…", 18, MUTED, (150, 137))
            self._draw_cards(None)
        else:
            condition, icon = condition_for(weather.weather_code, weather.is_day)
            self._draw_icon(icon, (60, 98), 84)
            self._text(f"{rounded(weather.temperature)}{weather.temperature_unit}", 64, self.theme["accent"], (150, 66))
            self._text(condition, 26, TEXT, (153, 128))
            secondary = (f"Feels {rounded(weather.apparent_temperature)}°  "
                         f"H {rounded(weather.high)}°  L {rounded(weather.low)}°")
            self._text(secondary, 18, MUTED, (153, 153))
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
        cards = ((8, 186, 150, 110), (165, 186, 150, 110), (322, 186, 150, 110))
        for rect in cards:
            pygame.draw.rect(self.surface, self.theme["card"], rect, border_radius=10)
        self._text("WIND", 16, MUTED, (20, 198))
        self._text("UV NOW", 16, MUTED, (177, 198))
        self._text("RAIN NOW", 16, MUTED, (334, 198))
        if not weather:
            for x in (20, 177, 334): self._text("—", 34, TEXT, (x, 229))
            return
        wind = f"{rounded(weather.wind_speed)} {weather.wind_unit}"
        self._text(wind, 28 if len(wind) < 10 else 24, TEXT, (20, 226))
        direction = compass_direction(weather.wind_direction)
        self._text(f"{direction} · gust {rounded(weather.wind_gusts)}", 16, MUTED, (20, 265))
        self._text(str(round(weather.uv_index, 1)), 34, self.theme["accent"], (177, 225))
        self._text(uv_risk(weather.uv_index), 16, MUTED, (177, 265))
        self._text(f"{weather.precipitation_probability}%", 34, self.theme["accent"], (334, 225))
        self._text("this hour", 16, MUTED, (334, 265))

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
