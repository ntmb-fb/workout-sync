import struct

import pytest

from workout_sync import polar
from workout_sync.polar import build_messages, resample


def decode(buf: bytes) -> dict[int, list]:
    """Minimal protobuf decoder: field number -> list of raw values."""
    out, i = {}, 0

    def varint():
        nonlocal i
        n = shift = 0
        while True:
            b = buf[i]
            i += 1
            n |= (b & 0x7F) << shift
            shift += 7
            if not b & 0x80:
                return n

    while i < len(buf):
        key = varint()
        field, wire = key >> 3, key & 7
        if wire == 0:
            v = varint()
        elif wire == 1:
            v = struct.unpack_from("<d", buf, i)[0]
            i += 8
        elif wire == 5:
            v = struct.unpack_from("<f", buf, i)[0]
            i += 4
        elif wire == 2:
            n = varint()
            v = buf[i:i + n]
            i += n
        else:
            raise ValueError(wire)
        out.setdefault(field, []).append(v)
    return out


def packed_varints(buf: bytes) -> list[int]:
    out, n, shift = [], 0, 0
    for b in buf:
        n |= (b & 0x7F) << shift
        shift += 7
        if not b & 0x80:
            out.append(n)
            n = shift = 0
    return out


def test_session_and_exercise(workout):
    m = build_messages(workout)
    s = decode(m["training_session"])
    assert s[2] == [1]  # one exercise
    assert s[4] == [b"Polar Beat"]
    assert abs(s[6][0] - 2520) < 0.01
    start = decode(s[1][0])
    date, tod = decode(start[1][0]), decode(start[2][0])
    # 06:00 UTC + 2 h offset, written as local time with the offset in minutes
    assert (date[1], date[2], date[3]) == ([2026], [10], [20])
    assert tod[1] == [8] and start[4] == [120]

    ex = decode(m["exercise_base"])
    assert decode(ex[3][0])[1] == [1]  # Polar sport: running
    assert abs(ex[12][0] - 55.67) < 1e-9


def test_samples_are_one_second(workout):
    m = build_messages(workout)
    samples = decode(m["samples"])
    hr = packed_varints(samples[2][0])
    assert len(hr) == 900 and hr[0] == 120
    route = decode(m["route"])
    n = len(route[2][0]) // 8
    assert n == 900  # 0..899 s, pause interpolated
    lat = struct.unpack(f"<{n}d", route[2][0])
    assert lat[0] == pytest.approx(55.67)


def test_resample_marks_pause_and_hr_gap(workout):
    s = resample(workout)
    assert s["speed"][630] == 0.0  # inside the 61 s gap
    assert s["speed"][100] > 0
    assert s["hr"][630] == 0  # no HR interpolated across the gap
    assert s["hr"][100] > 0


def test_upload_flow(workout):
    calls = []

    class Resp:
        def __init__(self, code, loc=None, content=b""):
            self.status_code, self.headers, self.content = code, {"Location": loc} if loc else {}, content

    class FakeHttp:
        headers = {}

        def post(self, url, data=None, **kw):
            calls.append(url.replace(polar.API, ""))
            if url.endswith("/login/user/associateToApp"):
                return Resp(200, content=b'{"id": 42}')
            if url.endswith("/training-sessions/create"):
                return Resp(201, "/v2/users/42/training-sessions/7")
            if url.endswith("/exercises/create"):
                return Resp(201, "/v2/users/42/training-sessions/7/exercises/9")
            return Resp(204)

    p = polar.Polar(session=FakeHttp())
    p.login("a@b.c", "x")
    status, url = p.upload(workout)
    assert status == "done" and url.endswith("/training-sessions/7")
    assert calls == ["/login/user/associateToApp", "/users/42/training-sessions/create",
                     "/users/42/training-sessions/7/exercises/create",
                     "/users/42/training-sessions/7/exercises/9/statistics",
                     "/users/42/training-sessions/7/exercises/9/samples",
                     "/users/42/training-sessions/7/exercises/9/route",
                     "/users/42/training-sessions/7/exercises/9/finalize?success=true"]


def test_upload_duplicate(workout):
    class Resp:
        status_code, headers, content = 200, {}, b'{"id": 1}'

    class FakeHttp:
        headers = {}

        def post(self, url, **kw):
            return Resp()

    p = polar.Polar(session=FakeHttp())
    p.login("a", "b")
    assert p.upload(workout) == ("duplicate", "")
