import threading

from fastapi.testclient import TestClient

from workout_sync.app import WEBHOOK_ID, create_app
from workout_sync.sync import Syncer

from .conftest import strava_detail


class FakeSubs:
    """Stands in for Strava's push_subscriptions API."""

    def __init__(self, subs=()):
        self.subs = list(subs)
        self.created, self.deleted = [], []

    def list_subscriptions(self):
        return self.subs

    def create_subscription(self, url, token):
        self.created.append((url, token))
        return 77

    def delete_subscription(self, sid):
        self.deleted.append(sid)


def setup(settings, subs=()):
    settings.webhook_url = "https://sync.example.com"
    app = create_app(settings, start_scheduler=False)
    strava = app.state.syncer.strava
    fake = FakeSubs(subs)
    for name in ("list_subscriptions", "create_subscription", "delete_subscription"):
        setattr(strava, name, getattr(fake, name))
    requests = []
    app.state.syncer.request = lambda trigger: requests.append(trigger) or True
    return TestClient(app), app, fake, requests


def test_validation_echoes_challenge(settings):
    c, *_ = setup(settings)
    q = {"hub.mode": "subscribe", "hub.challenge": "abc", "hub.verify_token": settings.webhook_verify_token}
    r = c.get("/strava/webhook", params=q)
    assert r.status_code == 200 and r.json() == {"hub.challenge": "abc"}
    assert c.get("/strava/webhook", params={**q, "hub.verify_token": "nope"}).status_code == 403


def test_off_without_webhook_url(settings):
    c = TestClient(create_app(settings, start_scheduler=False))
    assert c.get("/strava/webhook").status_code == 404
    assert c.post("/strava/webhook", json={}).status_code == 404


def test_create_event_starts_sync(settings):
    c, app, _, requests = setup(settings)
    app.state.db.set(WEBHOOK_ID, 77)
    app.state.db.set("strava_athlete_id", 5)
    ev = {"object_type": "activity", "aspect_type": "create", "object_id": 1, "owner_id": 5, "subscription_id": 77}
    assert c.post("/strava/webhook", json=ev).status_code == 200
    assert requests == ["webhook"]
    # ignored: updates, other athletes, other subscriptions
    c.post("/strava/webhook", json={**ev, "aspect_type": "update"})
    c.post("/strava/webhook", json={**ev, "owner_id": 6})
    c.post("/strava/webhook", json={**ev, "subscription_id": 1})
    assert requests == ["webhook"]


def test_setup_creates_and_replaces(settings):
    c, app, fake, _ = setup(settings, subs=[{"id": 3, "callback_url": "http://old/strava/webhook"}])
    r = c.post("/strava/webhook/setup", auth=("a", "pw"), follow_redirects=False)
    assert "active" in r.headers["location"]
    assert fake.deleted == [3]
    assert fake.created == [("https://sync.example.com/strava/webhook", settings.webhook_verify_token)]
    assert app.state.db.get(WEBHOOK_ID) == 77


def test_setup_reuses_existing(settings):
    c, app, fake, _ = setup(settings, subs=[{"id": 9, "callback_url": "https://sync.example.com/strava/webhook"}])
    c.post("/strava/webhook/setup", auth=("a", "pw"))
    assert not fake.created and not fake.deleted
    assert app.state.db.get(WEBHOOK_ID) == 9


def test_setup_needs_login(settings):
    c, *_ = setup(settings)
    assert c.post("/strava/webhook/setup").status_code == 401


def test_request_while_busy_runs_again(settings, fake_strava):
    from workout_sync import db as D
    db = D.DB(settings.data_dir / "t.db", settings.secret_key)
    db.set("sync_since", 0)
    gate, runs = threading.Event(), []

    def slow(w, path):
        gate.wait(5)
        return "done", ""

    s = Syncer(settings, db, fake_strava([strava_detail(1)]), {"garmin": slow})
    orig = s.run
    s.run = lambda trigger="manual", **kw: (runs.append(trigger), orig(trigger, **kw))[1]
    assert s.request("first")
    assert not s.request("webhook")  # busy: queued instead of dropped
    gate.set()
    for _ in range(100):
        if runs == ["first", "webhook"] and not s.running:
            break
        threading.Event().wait(0.05)
    assert runs == ["first", "webhook"]
