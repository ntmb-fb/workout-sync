"""Settings, read from environment variables (see .env.example)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _bool(v: str | None, default: bool = False) -> bool:
    if v is None or v == "":
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def _list(v: str | None) -> list[str]:
    return [x.strip() for x in (v or "").split(",") if x.strip()]


@dataclass
class Settings:
    data_dir: Path = Path("/data")
    public_url: str = "http://localhost:8080"
    admin_password: str = ""
    sync_token: str = ""
    secret_key: str = ""
    strava_client_id: str = ""
    strava_client_secret: str = ""
    sync_times: list[str] = field(default_factory=lambda: ["06:00", "12:00", "18:00", "23:00"])
    timezone: str = "UTC"
    device_filter: list[str] = field(default_factory=lambda: ["WorkOutDoors"])
    garmin_enabled: bool = True
    polar_enabled: bool = True
    garmin_spoof_device: bool = False
    garmin_spoof_product: int = 3113  # Forerunner 945
    max_attempts: int = 3
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    alert_to: str = ""

    @classmethod
    def from_env(cls) -> "Settings":
        e = os.environ.get
        s = cls(
            data_dir=Path(e("DATA_DIR", "/data")),
            public_url=e("PUBLIC_URL", "http://localhost:8080").rstrip("/"),
            admin_password=e("ADMIN_PASSWORD", ""),
            sync_token=e("SYNC_TOKEN", ""),
            secret_key=e("SECRET_KEY", ""),
            strava_client_id=e("STRAVA_CLIENT_ID", ""),
            strava_client_secret=e("STRAVA_CLIENT_SECRET", ""),
            timezone=e("TZ", "UTC"),
            garmin_enabled=_bool(e("GARMIN_ENABLED"), True),
            polar_enabled=_bool(e("POLAR_ENABLED"), True),
            garmin_spoof_device=_bool(e("GARMIN_SPOOF_DEVICE"), False),
            garmin_spoof_product=int(e("GARMIN_SPOOF_PRODUCT", "3113")),
            max_attempts=int(e("MAX_ATTEMPTS", "3")),
            smtp_host=e("SMTP_HOST", ""),
            smtp_port=int(e("SMTP_PORT", "587")),
            smtp_user=e("SMTP_USER", ""),
            smtp_password=e("SMTP_PASSWORD", ""),
            alert_to=e("ALERT_TO", ""),
        )
        if e("SYNC_TIMES") is not None:
            s.sync_times = _list(e("SYNC_TIMES"))
        if e("DEVICE_FILTER") is not None:
            s.device_filter = _list(e("DEVICE_FILTER"))
        return s

    def check(self) -> None:
        missing = [n for n, v in (("ADMIN_PASSWORD", self.admin_password),
                                  ("SYNC_TOKEN", self.sync_token),
                                  ("SECRET_KEY", self.secret_key)) if not v]
        if missing:
            raise SystemExit(f"Missing required settings: {', '.join(missing)}")

    @property
    def targets(self) -> list[str]:
        return [t for t, on in (("garmin", self.garmin_enabled), ("polar", self.polar_enabled)) if on]
