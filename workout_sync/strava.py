"""Strava API v3: OAuth, listing activities and turning one into a Workout."""
from __future__ import annotations

import time
from datetime import datetime, timezone
from urllib.parse import urlencode

import requests

from .db import DB
from .model import Lap, Sample, Workout

API = "https://www.strava.com/api/v3"
OAUTH = "https://www.strava.com/oauth"
STREAM_KEYS = "time,latlng,distance,altitude,heartrate,cadence,watts,velocity_smooth,moving"
TOKEN_KEY = "strava_token"


class StravaError(RuntimeError):
    pass


class Strava:
    def __init__(self, db: DB, client_id: str, client_secret: str, redirect_uri: str,
                 session: requests.Session | None = None):
        self.db = db
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
        self.http = session or requests.Session()

    # ---- OAuth

    @property
    def connected(self) -> bool:
        return self.db.get_secret(TOKEN_KEY) is not None

    @property
    def athlete_name(self) -> str:
        return self.db.get("strava_athlete", "")

    def authorize_url(self, state: str) -> str:
        return f"{OAUTH}/authorize?" + urlencode({
            "client_id": self.client_id, "redirect_uri": self.redirect_uri, "response_type": "code",
            "approval_prompt": "auto", "scope": "activity:read_all", "state": state})

    def exchange_code(self, code: str) -> None:
        tok = self._token_request(grant_type="authorization_code", code=code)
        a = tok.get("athlete") or {}
        self.db.set("strava_athlete", f"{a.get('firstname', '')} {a.get('lastname', '')}".strip())

    def disconnect(self) -> None:
        self.db.delete(TOKEN_KEY)
        self.db.delete("strava_athlete")

    def _token_request(self, **params) -> dict:
        r = self.http.post(f"{OAUTH}/token", data={
            "client_id": self.client_id, "client_secret": self.client_secret, **params}, timeout=30)
        if r.status_code != 200:
            raise StravaError(f"Strava token request failed: HTTP {r.status_code} {r.text[:200]}")
        tok = r.json()
        self.db.set_secret(TOKEN_KEY, {k: tok[k] for k in ("access_token", "refresh_token", "expires_at")})
        return tok

    def _access_token(self) -> str:
        tok = self.db.get_secret(TOKEN_KEY)
        if tok is None:
            raise StravaError("Strava is not connected")
        if tok["expires_at"] - 300 < time.time():
            tok = self._token_request(grant_type="refresh_token", refresh_token=tok["refresh_token"])
        return tok["access_token"]

    def _get(self, path: str, **params):
        r = self.http.get(f"{API}{path}", params=params, timeout=60,
                          headers={"Authorization": f"Bearer {self._access_token()}"})
        if r.status_code == 404:
            return None
        if r.status_code == 429:
            raise StravaError("Strava rate limit reached, will retry on the next sync")
        if r.status_code != 200:
            raise StravaError(f"Strava GET {path}: HTTP {r.status_code} {r.text[:200]}")
        return r.json()

    # ---- data

    def list_activities(self, after: float, before: float | None = None) -> list[dict]:
        out, page = [], 1
        while True:
            params = {"after": int(after), "page": page, "per_page": 100}
            if before:
                params["before"] = int(before)
            batch = self._get("/athlete/activities", **params) or []
            out += batch
            if len(batch) < 100:
                return out
            page += 1

    def activity(self, activity_id: int) -> dict:
        a = self._get(f"/activities/{activity_id}")
        if a is None:
            raise StravaError(f"Strava activity {activity_id} not found")
        return a

    def workout(self, activity_id: int, detail: dict | None = None) -> Workout:
        detail = detail or self.activity(activity_id)
        streams = self._get(f"/activities/{activity_id}/streams", keys=STREAM_KEYS, key_by_type="true") or {}
        laps = self._get(f"/activities/{activity_id}/laps") or []
        return to_workout(detail, streams, laps)


def _stream(streams, key) -> list:
    # key_by_type=true gives a dict; without it Strava returns a list of {type, data}
    if isinstance(streams, list):
        streams = {s["type"]: s for s in streams}
    s = streams.get(key)
    return s["data"] if s else []


def to_workout(a: dict, streams, laps: list[dict]) -> Workout:
    times = _stream(streams, "time")
    cols = {k: _stream(streams, k) for k in
            ("latlng", "distance", "altitude", "heartrate", "cadence", "watts", "velocity_smooth", "moving")}

    def at(key, i):
        col = cols[key]
        return col[i] if i < len(col) else None

    samples = []
    for i, t in enumerate(times):
        ll = at("latlng", i)
        moving = at("moving", i)
        samples.append(Sample(
            t=int(t),
            lat=ll[0] if ll else None, lon=ll[1] if ll else None,
            alt=at("altitude", i), dist=at("distance", i),
            hr=at("heartrate", i), cadence=at("cadence", i), watts=at("watts", i),
            speed=at("velocity_smooth", i),
            moving=True if moving is None else bool(moving)))

    out_laps = []
    for lp in laps:
        si = lp.get("start_index") or 0
        start = int(times[si]) if si < len(times) else 0
        out_laps.append(Lap(
            start=start, elapsed=int(lp.get("elapsed_time") or 0), moving=int(lp.get("moving_time") or 0),
            distance=float(lp.get("distance") or 0), avg_hr=lp.get("average_heartrate"),
            max_hr=lp.get("max_heartrate"), avg_cadence=lp.get("average_cadence"),
            avg_watts=lp.get("average_watts")))

    elev_gain = a.get("total_elevation_gain")
    descent = None
    alts = [s.alt for s in samples if s.alt is not None]
    if alts:
        descent = sum(max(p - c, 0) for p, c in zip(alts, alts[1:]))

    start = datetime.fromisoformat(a["start_date"].replace("Z", "+00:00")).astimezone(timezone.utc)
    return Workout(
        id=int(a["id"]), name=a.get("name") or "", sport_type=a.get("sport_type") or a.get("type") or "",
        start=start, utc_offset=int(a.get("utc_offset") or 0),
        elapsed=int(a.get("elapsed_time") or 0), moving=int(a.get("moving_time") or 0),
        distance=float(a.get("distance") or 0), calories=a.get("calories"),
        ascent=elev_gain, descent=descent,
        avg_hr=a.get("average_heartrate"), max_hr=a.get("max_heartrate"),
        avg_cadence=a.get("average_cadence"), avg_watts=a.get("average_watts"),
        max_watts=a.get("max_watts"), max_speed=a.get("max_speed"),
        trainer=bool(a.get("trainer")), device_name=a.get("device_name") or "",
        samples=samples, laps=out_laps)
