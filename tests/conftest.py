from datetime import datetime, timezone

import pytest

from workout_sync.config import Settings
from workout_sync.model import Lap, Sample, Workout
from workout_sync.strava import to_workout


def strava_detail(aid=1001, **kw):
    d = {"id": aid, "name": "Morning Run", "sport_type": "Run", "start_date": "2026-10-20T06:00:00Z",
         "utc_offset": 7200.0, "elapsed_time": 900, "moving_time": 840, "distance": 2520.0,
         "calories": 210.0, "total_elevation_gain": 12.0, "average_heartrate": 131.0,
         "max_heartrate": 150.0, "average_cadence": 85.0, "max_speed": 4.1, "trainer": False,
         "device_name": "WorkOutDoors", "manual": False}
    d.update(kw)
    return d


def strava_streams(n=900, pause=(600, 660)):
    t = [i for i in range(n) if not (pause[0] <= i < pause[1])]
    return {
        "time": {"data": t},
        "latlng": {"data": [[55.67 + i * 1e-5, 12.56] for i in t]},
        "distance": {"data": [i * 2.8 for i in t]},
        "altitude": {"data": [10 + (i % 50) * 0.2 for i in t]},
        "heartrate": {"data": [120 + i % 25 for i in t]},
        "cadence": {"data": [85 for _ in t]},
        "velocity_smooth": {"data": [2.8 for _ in t]},
        "moving": {"data": [True for _ in t]},
    }


def strava_laps():
    return [{"start_index": 0, "elapsed_time": 450, "moving_time": 450, "distance": 1260, "average_heartrate": 128},
            {"start_index": 450, "elapsed_time": 450, "moving_time": 390, "distance": 1260, "average_heartrate": 134}]


@pytest.fixture
def workout() -> Workout:
    return to_workout(strava_detail(), strava_streams(), strava_laps())


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(data_dir=tmp_path, admin_password="pw", sync_token="tok", secret_key="k" * 32,
                    strava_client_id="1", strava_client_secret="s", sync_times=[])


class FakeStrava:
    connected = True

    def __init__(self, details):
        self.details = {d["id"]: d for d in details}
        self.calls = []

    def list_activities(self, after, before=None):
        self.calls.append(("list", after, before))
        return [{"id": i} for i in self.details]

    def activity(self, aid):
        return self.details[aid]

    def workout(self, aid, detail=None):
        return to_workout(self.details[aid], strava_streams(), strava_laps())


@pytest.fixture
def fake_strava():
    return FakeStrava


__all__ = ["Lap", "Sample", "Workout", "datetime", "timezone"]
