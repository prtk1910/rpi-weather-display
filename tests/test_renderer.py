from pathlib import Path

import pygame
import pytest

from weather_display.preview import NOW, event_sample, sample
from weather_display.renderer import save_event_preview, save_preview


@pytest.mark.parametrize("scenario", ["day","night","rain","fog","extreme","long-location","stale","no-data"])
def test_preview_scenarios_are_exact_size(tmp_path, scenario):
    settings,weather,error=sample(scenario); path=tmp_path/f"{scenario}.png"
    save_preview(path,settings,weather,NOW,error)
    image=pygame.image.load(path)
    assert image.get_size()==(480,320) and path.stat().st_size>1000

def test_preview_is_deterministic(tmp_path):
    settings,weather,error=sample("day"); a,b=tmp_path/"a.png",tmp_path/"b.png"
    save_preview(a,settings,weather,NOW,error); save_preview(b,settings,weather,NOW,error)
    assert a.read_bytes()==b.read_bytes()

def test_weather_scenarios_change_color_theme(tmp_path):
    colors=[]
    for scenario in ("day","rain","night"):
        settings,weather,error=sample(scenario); path=tmp_path/f"{scenario}.png"
        save_preview(path,settings,weather,NOW,error)
        colors.append(pygame.image.load(path).get_at((0,0))[:3])
    assert len(set(colors))==3

@pytest.mark.parametrize("scenario", ["events","events-long","events-stale","events-unavailable"])
def test_event_preview_scenarios_are_deterministic_and_exact_size(tmp_path, scenario):
    settings,selection,fetched,error=event_sample(scenario); a,b=tmp_path/"a.png",tmp_path/"b.png"
    save_event_preview(a,settings,selection,NOW,fetched,error)
    save_event_preview(b,settings,selection,NOW,fetched,error)
    assert pygame.image.load(a).get_size()==(480,320)
    assert a.stat().st_size>1000 and a.read_bytes()==b.read_bytes()
