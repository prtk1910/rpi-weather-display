import threading

from weather_display.state import StateStore
from weather_display.weather import OpenMeteoProvider, WeatherService
from weather_display.web import create_app

from test_weather import Response, Session


def make_client(tmp_path):
    store=StateStore(tmp_path); provider=OpenMeteoProvider(Session(Response({"results":[]})))
    weather=WeatherService(store,provider); event=threading.Event()
    app=create_app(store,weather,provider,"test-pin",event,lambda:"rendering")
    app.config.update(TESTING=True)
    return app.test_client(), store, event

def login(client, pin="test-pin"): return client.post("/login",data={"pin":pin})

def csrf(client):
    with client.session_transaction() as session: return session["csrf"]

def test_authentication_and_no_pin_in_response(tmp_path):
    client,_,_=make_client(tmp_path)
    assert client.get("/api/settings").status_code==401
    response=login(client)
    assert response.status_code==302
    assert b"test-pin" not in client.get("/settings").data

def test_update_requires_csrf_and_triggers_refresh(tmp_path):
    client,store,event=make_client(tmp_path); login(client)
    value={"location_label":"Oakland","latitude":37.8,"longitude":-122.2,
           "timezone":"America/Los_Angeles","units":"imperial","clock_format":"12h"}
    assert client.put("/api/settings",json=value).status_code==403
    response=client.put("/api/settings",json=value,headers={"X-CSRF-Token":csrf(client)})
    assert response.status_code==200 and event.is_set()
    assert store.load_settings().units=="imperial"

def test_cross_origin_mutation_rejected(tmp_path):
    client,_,_=make_client(tmp_path); login(client)
    response=client.put("/api/settings",json={},headers={"X-CSRF-Token":csrf(client),
                         "Origin":"http://evil.example"})
    assert response.status_code==403

def test_invalid_settings_do_not_replace_existing(tmp_path):
    client,store,_=make_client(tmp_path); login(client); before=store.load_settings()
    response=client.put("/api/settings",json={"units":"kelvin"},headers={"X-CSRF-Token":csrf(client)})
    assert response.status_code==400 and store.load_settings()==before

def test_failed_login_throttling(tmp_path):
    client,_,_=make_client(tmp_path)
    for _ in range(5): assert login(client,"bad").status_code==200
    assert login(client,"bad").status_code==429

def test_health_is_public_and_contains_status(tmp_path):
    client,_,_=make_client(tmp_path)
    assert client.get("/healthz").json["display"]=="rendering"
