"""Build a FIT activity file from a Workout (for the Garmin upload)."""
from __future__ import annotations

from fit_tool.fit_file_builder import FitFileBuilder
from fit_tool.profile.messages.activity_message import ActivityMessage
from fit_tool.profile.messages.device_info_message import DeviceInfoMessage
from fit_tool.profile.messages.event_message import EventMessage
from fit_tool.profile.messages.file_id_message import FileIdMessage
from fit_tool.profile.messages.lap_message import LapMessage
from fit_tool.profile.messages.record_message import RecordMessage
from fit_tool.profile.messages.session_message import SessionMessage
from fit_tool.profile.profile_type import Activity, Event, EventType, FileType, Manufacturer

from .model import Lap, Workout
from .sports import map_sport

PAUSE_GAP_S = 10  # a gap this long between samples is written as a timer pause
FIT_EPOCH_S = 631065600  # 1989-12-31T00:00:00Z


def _mean(vals):
    vals = [v for v in vals if v is not None]
    return sum(vals) / len(vals) if vals else None


def _event(ts_ms: int, event_type: EventType) -> EventMessage:
    m = EventMessage()
    m.event = Event.TIMER
    m.event_type = event_type
    m.timestamp = ts_ms
    return m


def build_fit(w: Workout, *, spoof_garmin: bool = False, garmin_product: int = 3113) -> bytes:
    start_ms = int(w.start.timestamp() * 1000)
    end_s = max([w.elapsed] + [s.t for s in w.samples])
    end_ms = start_ms + end_s * 1000
    sm = map_sport(w.sport_type, trainer=w.trainer, has_gps=w.has_gps)
    serial = w.id % 0xFFFFFFFF or 1  # stable per activity, so Garmin can spot re-uploads

    b = FitFileBuilder(auto_define=True, min_string_size=50)

    fid = FileIdMessage()
    fid.type = FileType.ACTIVITY
    fid.time_created = start_ms
    fid.serial_number = serial
    dev = DeviceInfoMessage()
    dev.timestamp = start_ms
    dev.device_index = 0
    dev.serial_number = serial
    if spoof_garmin:
        fid.manufacturer = dev.manufacturer = Manufacturer.GARMIN.value
        fid.product = dev.product = garmin_product
    else:
        fid.manufacturer = dev.manufacturer = Manufacturer.DEVELOPMENT.value
        fid.product = dev.product = 0
        fid.product_name = dev.product_name = (w.device_name or "Apple Watch")[:40]
    b.add(fid)
    b.add(dev)

    b.add(_event(start_ms, EventType.START))
    prev_t = None
    records = []
    for s in w.samples:
        if prev_t is not None and s.t - prev_t >= PAUSE_GAP_S:
            records.append(_event(start_ms + prev_t * 1000, EventType.STOP_ALL))
            records.append(_event(start_ms + s.t * 1000, EventType.START))
        prev_t = s.t
        r = RecordMessage()
        r.timestamp = start_ms + s.t * 1000
        if s.lat is not None and s.lon is not None:
            r.position_lat = s.lat
            r.position_long = s.lon
        if s.alt is not None:
            r.altitude = s.alt
        if s.dist is not None:
            r.distance = s.dist
        if s.speed is not None:
            r.speed = s.speed
        if s.hr:
            r.heart_rate = int(s.hr)
        if s.cadence is not None:
            r.cadence = int(s.cadence)
        if s.watts is not None:
            r.power = int(s.watts)
        records.append(r)
    b.add_all(records)
    b.add(_event(end_ms, EventType.STOP_ALL))

    laps = w.laps or [Lap(start=0, elapsed=end_s, moving=w.moving or end_s, distance=w.distance,
                          avg_hr=w.avg_hr, max_hr=w.max_hr, avg_cadence=w.avg_cadence, avg_watts=w.avg_watts)]
    for i, lp in enumerate(laps):
        m = LapMessage()
        m.message_index = i
        m.event = Event.LAP
        m.event_type = EventType.STOP
        m.start_time = start_ms + lp.start * 1000
        m.timestamp = start_ms + (lp.start + lp.elapsed) * 1000
        m.total_elapsed_time = lp.elapsed
        m.total_timer_time = lp.moving or lp.elapsed
        m.total_distance = lp.distance
        m.sport = sm.fit_sport
        m.sub_sport = sm.fit_sub_sport
        if lp.avg_hr:
            m.avg_heart_rate = round(lp.avg_hr)
        if lp.max_hr:
            m.max_heart_rate = round(lp.max_hr)
        if lp.avg_cadence:
            m.avg_cadence = round(lp.avg_cadence)
        if lp.avg_watts:
            m.avg_power = round(lp.avg_watts)
        if lp.moving and lp.distance:
            m.avg_speed = lp.distance / lp.moving
        b.add(m)

    timer = w.moving or end_s
    s = SessionMessage()
    s.message_index = 0
    s.event = Event.SESSION
    s.event_type = EventType.STOP
    s.start_time = start_ms
    s.timestamp = end_ms
    s.total_elapsed_time = end_s
    s.total_timer_time = timer
    s.total_distance = w.distance
    s.sport = sm.fit_sport
    s.sub_sport = sm.fit_sub_sport
    s.first_lap_index = 0
    s.num_laps = len(laps)
    avg_hr = w.avg_hr or _mean([x.hr for x in w.samples if x.hr])
    max_hr = w.max_hr or max([x.hr for x in w.samples if x.hr], default=None)
    if avg_hr:
        s.avg_heart_rate = round(avg_hr)
    if max_hr:
        s.max_heart_rate = round(max_hr)
    if w.calories:
        s.total_calories = round(w.calories)
    if w.ascent is not None:
        s.total_ascent = round(w.ascent)
    if w.descent is not None:
        s.total_descent = round(w.descent)
    if w.avg_cadence:
        s.avg_cadence = round(w.avg_cadence)
    if w.avg_watts:
        s.avg_power = round(w.avg_watts)
    if w.max_watts:
        s.max_power = round(w.max_watts)
    if w.distance and timer:
        s.avg_speed = w.distance / timer
    if w.max_speed:
        s.max_speed = w.max_speed
    first = next((x for x in w.samples if x.lat is not None), None)
    if first:
        s.start_position_lat = first.lat
        s.start_position_long = first.lon
    b.add(s)

    act = ActivityMessage()
    act.timestamp = end_ms
    act.total_timer_time = timer
    act.num_sessions = 1
    act.type = Activity.MANUAL
    act.event = Event.ACTIVITY
    act.event_type = EventType.STOP
    # fit_tool takes this one raw: seconds since the FIT epoch, in local time
    act.local_timestamp = end_ms // 1000 + w.utc_offset - FIT_EPOCH_S
    b.add(act)

    return b.build().to_bytes()
