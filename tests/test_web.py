from fastapi.testclient import TestClient

from workout_sync.app import create_app


def client(settings):
    app = create_app(settings, start_scheduler=False)
    app.state.syncer.start_background = lambda *a, **kw: True  # don't hit Strava
    return TestClient(app)


def test_health_is_open(settings):
    assert client(settings).get("/health").json() == {"ok": True}


def test_ui_needs_password(settings):
    c = client(settings)
    assert c.get("/").status_code == 401
    assert c.get("/", auth=("admin", "wrong")).status_code == 401
    r = c.get("/", auth=("admin", "pw"))
    assert r.status_code == 200 and "Connect Strava" in r.text


def test_sync_url_token(settings):
    c = client(settings)
    assert c.post("/sync").status_code == 401
    assert c.post("/sync?token=nope").status_code == 401
    r = c.post("/sync?token=tok")
    assert r.status_code == 202 and r.json() == {"status": "started"}
    assert c.post("/sync", headers={"X-Sync-Token": "tok"}).status_code == 202


def test_backfill_validates_dates(settings):
    c = client(settings)
    r = c.post("/backfill", data={"date_from": "nope", "date_to": "2026-10-01"}, auth=("a", "pw"),
               follow_redirects=False)
    assert r.status_code == 303 and "Dates" in r.headers["location"]


def test_strava_callback_checks_state(settings):
    c = client(settings)
    r = c.get("/strava/callback?code=x&state=bad&scope=read,activity:read_all", auth=("a", "pw"),
              follow_redirects=False)
    assert "expired" in r.headers["location"]


def test_starts_with_scheduler(settings):
    settings.sync_times = ["06:00", "18:30"]
    app = create_app(settings)
    with TestClient(app) as c:  # runs the lifespan, so the scheduler starts
        r = c.get("/", auth=("a", "pw"))
        assert r.status_code == 200 and "next 20" in r.text
