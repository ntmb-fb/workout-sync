"""Upload a Workout to Polar Flow.

Polar has no public upload API. This uses the endpoints of the retired Polar Beat
phone app (www.polarremote.com/v2), which still accept training sessions as
protobuf messages. docs/polar-protocol.md lists the messages and fields used.
"""
from __future__ import annotations

import bisect
import json
import struct
from datetime import timedelta

import requests

from .model import Workout
from .sports import map_sport

API = "https://www.polarremote.com/v2"
# The Polar Beat Android app's public client id/key ("Polar Beat2 Android:Beat2AndroidApiKey")
APP_AUTH = "Basic UG9sYXIgQmVhdDIgQW5kcm9pZDpCZWF0MkFuZHJvaWRBcGlLZXk="
USER_AGENT = "PolarBeat-Android/3.4.4 Dalvik/2.1.0 (Linux; U; Android 6.0)"
MODEL_NAME = "Polar Beat"  # the server expects sessions from this app
PAUSE_GAP_S = 60  # longer gaps between samples count as standing still
HR_MAX_GAP_S = 15  # don't interpolate heart rate across longer gaps
MAX_HR = 210


class PolarError(RuntimeError):
    pass


# ---------------------------------------------------------------- protobuf writer

def _varint(n: int) -> bytes:
    if n < 0:
        n += 1 << 64
    out = bytearray()
    while True:
        b, n = n & 0x7F, n >> 7
        out.append(b | 0x80 if n else b)
        if not n:
            return bytes(out)


class Pb:
    """Tiny proto2 writer: fields are written in call order, None values are skipped."""

    def __init__(self):
        self.buf = bytearray()

    def _key(self, field: int, wire: int):
        self.buf += _varint(field << 3 | wire)

    def uint(self, field, v):
        if v is not None:
            self._key(field, 0)
            self.buf += _varint(int(v))
        return self

    def float(self, field, v):
        if v is not None:
            self._key(field, 5)
            self.buf += struct.pack("<f", v)
        return self

    def double(self, field, v):
        if v is not None:
            self._key(field, 1)
            self.buf += struct.pack("<d", v)
        return self

    def bytes(self, field, data: bytes):
        self._key(field, 2)
        self.buf += _varint(len(data)) + data
        return self

    def string(self, field, s):
        return self if s is None else self.bytes(field, s.encode())

    def msg(self, field, sub: "Pb | None"):
        return self if sub is None else self.bytes(field, bytes(sub.buf))

    def packed(self, field, values, kind: str):
        if not values:
            return self
        if kind == "uint":
            data = b"".join(_varint(int(v)) for v in values)
        elif kind == "sint":  # zigzag
            data = b"".join(_varint((int(v) << 1) ^ (int(v) >> 63)) for v in values)
        else:  # "f" float32, "d" float64
            data = struct.pack(f"<{len(values)}{kind}", *values)
        return self.bytes(field, data)

    def __bytes__(self):
        return bytes(self.buf)


def pb_duration(seconds: float) -> Pb:
    ms = int(round(seconds * 1000))
    return Pb().uint(1, ms // 3_600_000).uint(2, ms // 60_000 % 60).uint(3, ms // 1000 % 60).uint(4, ms % 1000)


def pb_local_datetime(w: Workout) -> Pb:
    local = w.start + timedelta(seconds=w.utc_offset)
    date = Pb().uint(1, local.year).uint(2, local.month).uint(3, local.day)
    tod = Pb().uint(1, local.hour).uint(2, local.minute).uint(3, local.second).uint(4, local.microsecond // 1000)
    return Pb().msg(1, date).msg(2, tod).uint(3, 1).uint(4, w.utc_offset // 60)


# ---------------------------------------------------------------- messages

def _interp(xs, ys, x):
    i = bisect.bisect_right(xs, x)
    if i == 0:
        return ys[0]
    if i == len(xs):
        return ys[-1]
    x0, x1 = xs[i - 1], xs[i]
    return ys[i - 1] + (ys[i] - ys[i - 1]) * (x - x0) / (x1 - x0)


def _series(w: Workout, attr: str):
    pts = [(s.t, getattr(s, attr)) for s in w.samples if getattr(s, attr) is not None]
    return [p[0] for p in pts], [float(p[1]) for p in pts]


def resample(w: Workout) -> dict:
    """1-second series, the way Polar Beat recorded them."""
    if not w.samples:
        return {}
    end = w.samples[-1].t
    secs = list(range(0, end + 1))
    t_all = [s.t for s in w.samples]
    out = {"t": secs}

    lt, lat = _series(w, "lat")
    _, lon = _series(w, "lon")
    if lt:
        out["lat"] = [_interp(lt, lat, t) for t in secs]
        out["lon"] = [_interp(lt, lon, t) for t in secs]
    at, alt = _series(w, "alt")
    if at:
        out["alt"] = [_interp(at, alt, t) for t in secs]
    dt, dist = _series(w, "dist")
    if dt:
        d = [_interp(dt, dist, t) for t in secs]
        for i in range(1, len(d)):  # keep cumulative distance non-decreasing
            d[i] = max(d[i], d[i - 1])
        out["dist"] = d
        speed = [0.0]
        for i in range(1, len(secs)):
            j = bisect.bisect_right(t_all, secs[i])
            paused = 0 < j < len(t_all) and t_all[j] - t_all[j - 1] > PAUSE_GAP_S
            speed.append(0.0 if paused else min((d[i] - d[i - 1]) * 3.6, 200.0))
        out["speed"] = speed  # km/h
    ht, hr = _series(w, "hr")
    if ht:
        vals = []
        for t in secs:
            j = bisect.bisect_left(ht, t)
            near = min((abs(ht[k] - t) for k in (j - 1, j) if 0 <= k < len(ht)), default=None)
            vals.append(round(_interp(ht, hr, t)) if near is not None and near <= HR_MAX_GAP_S else 0)
        if any(vals):
            out["hr"] = vals
    return out


def build_messages(w: Workout) -> dict[str, bytes | None]:
    sm = map_sport(w.sport_type, trainer=w.trainer, has_gps=w.has_gps)
    series = resample(w)
    duration = max(w.elapsed, series["t"][-1] if series else 0)
    start = pb_local_datetime(w)
    calories = round(w.calories) if w.calories else None
    note = f"{w.name} (synced from Strava)" if w.name else "Synced from Strava"

    session = (Pb().msg(1, start).uint(2, 1).string(4, MODEL_NAME).msg(5, pb_duration(duration))
               .float(6, w.distance).uint(7, calories).msg(13, Pb().string(1, note[:500])))

    exercise = (Pb().msg(1, start).msg(2, pb_duration(duration)).msg(3, Pb().uint(1, sm.polar))
                .float(4, w.distance).uint(5, calories).float(10, w.ascent).float(11, w.descent))
    if "lat" in series:
        exercise.double(12, series["lat"][0]).double(13, series["lon"][0])

    stats = Pb()
    hr_vals = [v for v in series.get("hr", []) if v]
    if hr_vals:
        stats.msg(1, Pb().uint(1, min(hr_vals)).uint(2, round(sum(hr_vals) / len(hr_vals)))
                  .uint(3, min(max(hr_vals), MAX_HR)))
    elif w.avg_hr:
        stats.msg(1, Pb().uint(2, round(w.avg_hr)).uint(3, min(round(w.max_hr or w.avg_hr), MAX_HR)))
    timer = w.moving or duration
    if timer and w.distance:
        avg = w.distance / timer * 3.6
        mx = max((w.max_speed or 0) * 3.6, avg)
        stats.msg(2, Pb().float(1, avg).float(2, min(mx, 350.0)))
    if "alt" in series:
        a = series["alt"]
        stats.msg(4, Pb().float(1, min(a)).float(2, sum(a) / len(a)).float(3, max(a)))

    samples = route = None
    if len(series.get("t", [])) > 2:
        samples = (Pb().msg(1, pb_duration(1))
                   .packed(2, series.get("hr"), "uint")
                   .packed(6, series.get("alt"), "f")
                   .packed(9, series.get("speed"), "f")
                   .packed(11, series.get("dist"), "f"))
        if "lat" in series:
            route = (Pb().packed(1, [t * 1000 for t in series["t"]], "uint")
                     .packed(2, series["lat"], "d")
                     .packed(3, series["lon"], "d")
                     .packed(4, [round(v) for v in series.get("alt", [0.0] * len(series["t"]))], "sint"))

    return {"training_session": bytes(session), "exercise_base": bytes(exercise),
            "statistics": bytes(stats) if stats.buf else None,
            "samples": bytes(samples) if samples else None,
            "route": bytes(route) if route else None}


# ---------------------------------------------------------------- API

class Polar:
    def __init__(self, session: requests.Session | None = None):
        self.http = session or requests.Session()
        self.http.headers.update({"Authorization": APP_AUTH, "User-Agent": USER_AGENT})
        self.user_id: int | None = None

    def _post(self, url: str, body: bytes, content_type: str):
        accept = "application/x-protobuf" if "protobuf" in content_type else "application/json"
        r = self.http.post(url, data=body, timeout=60, allow_redirects=False,
                           headers={"Content-Type": content_type, "Accept": accept})
        loc = r.headers.get("Location")
        if loc and loc.startswith("/"):
            loc = "https://www.polarremote.com" + loc
        return r.status_code, loc, r.content

    def login(self, email: str, password: str) -> int:
        code, _, data = self._post(f"{API}/login/user/associateToApp",
                                   json.dumps({"username": email, "password": password}).encode(),
                                   "application/json")
        if code != 200:
            raise PolarError(f"Polar login failed (HTTP {code})")
        self.user_id = int(json.loads(data)["id"])
        return self.user_id

    def upload(self, w: Workout) -> tuple[str, str]:
        """Returns ("done", session_url) or ("duplicate", "")."""
        if self.user_id is None:
            raise PolarError("not logged in")
        m = build_messages(w)
        pb = "application/x-protobuf"
        code, session_url, data = self._post(f"{API}/users/{self.user_id}/training-sessions/create",
                                             m["training_session"], pb)
        if code == 200:  # the server already has a session with this start time
            return "duplicate", ""
        if code != 201 or not session_url:
            raise PolarError(f"training-sessions/create: HTTP {code} {data[:200]!r}")
        code, ex_url, data = self._post(f"{session_url}/exercises/create", m["exercise_base"], pb)
        if code not in (200, 201) or not ex_url:
            raise PolarError(f"exercises/create: HTTP {code} {data[:200]!r}")
        for part in ("statistics", "samples", "route"):
            if m[part]:
                code, _, data = self._post(f"{ex_url}/{part}", m[part], pb)
                if code >= 300:
                    raise PolarError(f"{part}: HTTP {code} {data[:200]!r}")
        # The app finalizes the exercise; the server sometimes answers 404 there,
        # so also try the session and don't treat a failure as fatal.
        form = "application/x-www-form-urlencoded; charset=UTF-8"
        for url in (ex_url, session_url):
            if self._post(f"{url}/finalize?success=true", b"", form)[0] < 300:
                break
        return "done", session_url
