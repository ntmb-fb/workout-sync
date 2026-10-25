from collections import Counter

from fit_tool.fit_file import FitFile
from fit_tool.profile.profile_type import EventType, Manufacturer, Sport, SubSport

from workout_sync.fitfile import build_fit


def _messages(data):
    return [r.message for r in FitFile.from_bytes(data).records if type(r.message).__name__ != "DefinitionMessage"]


def test_fit_has_all_parts(workout):
    msgs = _messages(build_fit(workout))
    kinds = Counter(type(m).__name__ for m in msgs)
    assert kinds["RecordMessage"] == len(workout.samples)
    assert kinds["LapMessage"] == 2
    assert kinds["SessionMessage"] == kinds["ActivityMessage"] == kinds["FileIdMessage"] == 1

    session = next(m for m in msgs if type(m).__name__ == "SessionMessage")
    assert session.sport == Sport.RUNNING.value
    assert session.total_distance == 2520
    assert session.total_timer_time == 840
    assert session.avg_heart_rate == 131
    assert session.start_time == int(workout.start.timestamp() * 1000)

    rec = next(m for m in msgs if type(m).__name__ == "RecordMessage")
    assert abs(rec.position_lat - 55.67) < 1e-5
    assert rec.heart_rate == 120 and rec.cadence == 85


def test_pause_writes_timer_events(workout):
    events = [m for m in _messages(build_fit(workout)) if type(m).__name__ == "EventMessage"]
    # start, stop+start around the 60 s gap, final stop
    assert [e.event_type for e in events] == [EventType.START.value, EventType.STOP_ALL.value,
                                               EventType.START.value, EventType.STOP_ALL.value]


def test_device_labels(workout):
    fid = next(m for m in _messages(build_fit(workout)) if type(m).__name__ == "FileIdMessage")
    assert fid.manufacturer == Manufacturer.DEVELOPMENT.value
    assert fid.product_name == "WorkOutDoors"

    fid = next(m for m in _messages(build_fit(workout, spoof_garmin=True, garmin_product=3113))
               if type(m).__name__ == "FileIdMessage")
    assert fid.manufacturer == Manufacturer.GARMIN.value and fid.product == 3113


def test_indoor_workout_without_streams(workout):
    workout.samples, workout.laps = [], []
    workout.sport_type, workout.trainer = "Ride", True
    msgs = _messages(build_fit(workout))
    session = next(m for m in msgs if type(m).__name__ == "SessionMessage")
    assert session.sub_sport == SubSport.INDOOR_CYCLING.value
    assert session.total_elapsed_time == 900
