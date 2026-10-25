"""Service-neutral workout model. Strava data is converted into this, and the
FIT builder and the Polar uploader read from it."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Sample:
    t: int  # seconds since start
    lat: float | None = None
    lon: float | None = None
    alt: float | None = None  # metres
    dist: float | None = None  # cumulative metres
    hr: int | None = None
    cadence: int | None = None
    watts: int | None = None
    speed: float | None = None  # m/s
    moving: bool = True


@dataclass
class Lap:
    start: int  # seconds since workout start
    elapsed: int
    moving: int
    distance: float
    avg_hr: float | None = None
    max_hr: float | None = None
    avg_cadence: float | None = None
    avg_watts: float | None = None


@dataclass
class Workout:
    id: int
    name: str
    sport_type: str
    start: datetime  # timezone-aware UTC
    utc_offset: int  # seconds, local time minus UTC
    elapsed: int
    moving: int
    distance: float
    calories: float | None = None
    ascent: float | None = None
    descent: float | None = None
    avg_hr: float | None = None
    max_hr: float | None = None
    avg_cadence: float | None = None
    avg_watts: float | None = None
    max_watts: float | None = None
    max_speed: float | None = None
    trainer: bool = False
    device_name: str = ""
    samples: list[Sample] = field(default_factory=list)
    laps: list[Lap] = field(default_factory=list)

    @property
    def has_gps(self) -> bool:
        return any(s.lat is not None for s in self.samples)
