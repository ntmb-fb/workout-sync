from workout_sync import db as D
from workout_sync.sports import map_sport
from workout_sync.sync import Syncer, device_matches

from .conftest import strava_detail


def make(settings, fake_strava, details, uploaders):
    db = D.DB(settings.data_dir / "t.db", settings.secret_key)
    db.set("sync_since", 0)
    return db, Syncer(settings, db, fake_strava(details), uploaders)


def ok(name, log):
    def up(w, path):
        log.append((name, w.id, path.exists()))
        return "done", "x"
    return up


def test_uploads_once_to_each_target(settings, fake_strava):
    log = []
    db, s = make(settings, fake_strava, [strava_detail(1)], {"garmin": ok("garmin", log), "polar": ok("polar", log)})
    r = s.run()
    assert r["status"] == "ok" and r["new"] == 1 and r["uploaded"] == 2
    assert sorted(log) == [("garmin", 1, True), ("polar", 1, True)]
    s.run()  # second run: nothing new, nothing re-uploaded
    assert len(log) == 2
    row = db.activity(1)
    assert row["garmin_status"] == row["polar_status"] == D.DONE


def test_device_filter_and_manual(settings, fake_strava):
    log = []
    details = [strava_detail(1), strava_detail(2, device_name="Garmin Forerunner 965"),
               strava_detail(3, manual=True)]
    db, s = make(settings, fake_strava, details, {"garmin": ok("garmin", log), "polar": ok("polar", log)})
    s.run()
    assert {x[1] for x in log} == {1}
    assert db.activity(2)["garmin_status"] == D.SKIPPED
    assert "not in DEVICE_FILTER" in db.activity(2)["skipped_reason"]
    assert db.activity(3)["skipped_reason"] == "manual entry"


def test_retry_until_max_then_resend(settings, fake_strava, monkeypatch):
    monkeypatch.setattr("workout_sync.sync.send_alert", lambda *a: None)
    calls = []

    def bad(w, path):
        calls.append(1)
        raise RuntimeError("boom")

    db, s = make(settings, fake_strava, [strava_detail(1)], {"garmin": bad, "polar": ok("polar", [])})
    for _ in range(5):
        s.run()
    assert len(calls) == settings.max_attempts
    row = db.activity(1)
    assert row["garmin_status"] == D.FAILED and row["garmin_detail"] == "boom"
    assert row["polar_status"] == D.DONE

    db.reset(1, "garmin")
    s.run()
    assert len(calls) == settings.max_attempts + 1


def test_duplicate_is_recorded(settings, fake_strava):
    db, s = make(settings, fake_strava, [strava_detail(1)],
                 {"garmin": lambda w, p: ("duplicate", ""), "polar": lambda w, p: ("done", "")})
    s.run()
    assert db.activity(1)["garmin_status"] == D.DUPLICATE


def test_disabled_target_is_skipped(settings, fake_strava):
    settings.polar_enabled = False
    log = []
    db, s = make(settings, fake_strava, [strava_detail(1)], {"garmin": ok("garmin", log), "polar": ok("polar", log)})
    s.run()
    assert [x[0] for x in log] == ["garmin"]
    assert db.activity(1)["polar_status"] == D.SKIPPED


def test_device_matches():
    assert device_matches("WorkOutDoors", ["workoutdoors"])
    assert device_matches("Apple Watch Ultra", ["WorkOutDoors", "Apple Watch"])
    assert not device_matches("", ["WorkOutDoors"])
    assert device_matches("anything", [])


def test_sport_mapping():
    assert map_sport("Run").polar == 1
    assert map_sport("Run", trainer=True).polar == 17
    assert map_sport("Swim").polar == 103 and map_sport("Swim", has_gps=True).polar == 105
    assert map_sport("Ride", trainer=True).polar == 18
    assert map_sport("Unknownsport", has_gps=True).polar == 16
    assert map_sport("Unknownsport").polar == 83


def test_secrets_roundtrip(settings):
    db = D.DB(settings.data_dir / "t.db", settings.secret_key)
    db.set_secret("x", {"password": "hunter2"})
    assert db.get_secret("x") == {"password": "hunter2"}
    assert b"hunter2" not in (settings.data_dir / "t.db").read_bytes()
    other = D.DB(settings.data_dir / "t.db", "different-key")
    assert other.get_secret("x") is None
