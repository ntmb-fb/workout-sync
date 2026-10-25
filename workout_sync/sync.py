"""The sync run: find new Strava activities, then upload each to every target."""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from . import db as D
from .config import Settings
from .fitfile import build_fit
from .model import Workout
from .notify import send_alert

log = logging.getLogger(__name__)


class StravaSource(Protocol):
    connected: bool

    def list_activities(self, after: float, before: float | None = None) -> list[dict]: ...
    def activity(self, activity_id: int) -> dict: ...
    def workout(self, activity_id: int, detail: dict | None = None) -> Workout: ...


class Uploader(Protocol):
    def __call__(self, workout: Workout, fit_path: Path) -> tuple[str, str]: ...


def device_matches(device: str, filters: list[str]) -> bool:
    if not filters:
        return True
    d = (device or "").lower()
    return any(f.lower() in d for f in filters)


class Syncer:
    def __init__(self, settings: Settings, db: D.DB, strava: StravaSource, uploaders: dict[str, Uploader]):
        self.s = settings
        self.db = db
        self.strava = strava
        self.uploaders = uploaders  # target name -> upload function
        self._lock = threading.Lock()
        self.fit_dir = settings.data_dir / "fit"

    @property
    def running(self) -> bool:
        return self._lock.locked()

    def start_background(self, trigger: str, **kw) -> bool:
        """Start a run in a thread. False if one is already running."""
        if self.running:
            return False
        threading.Thread(target=self.run, args=(trigger,), kwargs=kw, daemon=True).start()
        return True

    def run(self, trigger: str = "manual", backfill: tuple[datetime, datetime] | None = None) -> dict:
        if not self._lock.acquire(blocking=False):
            return {"status": "busy"}
        started = time.time()
        summary = {"status": "ok", "trigger": trigger, "new": 0, "uploaded": 0, "failed": 0, "errors": []}
        try:
            if not self.strava.connected:
                raise RuntimeError("Strava is not connected")
            self._discover(summary, backfill)
            self._upload(summary)
        except Exception as e:
            log.exception("Sync failed")
            summary["status"] = "error"
            summary["errors"].append(str(e))
        finally:
            summary["started"] = started
            summary["seconds"] = round(time.time() - started, 1)
            self.db.set("last_run", summary)
            self._lock.release()
        if summary["errors"]:
            send_alert(self.s, f"{len(summary['errors'])} problem(s) during sync",
                       "\n".join(f"- {e}" for e in summary["errors"]))
        log.info("Sync done: %s", {k: v for k, v in summary.items() if k != "errors"})
        return summary

    def _discover(self, summary: dict, backfill: tuple[datetime, datetime] | None) -> None:
        if backfill:
            after, before = backfill[0].timestamp(), backfill[1].timestamp()
        else:
            after, before = self.db.get("sync_since", time.time()), None
        for a in self.strava.list_activities(after, before):
            aid = int(a["id"])
            if self.db.has_activity(aid):
                continue
            detail = self.strava.activity(aid)  # the list doesn't include device_name
            device = detail.get("device_name") or ""
            reason = None
            if detail.get("manual"):
                reason = "manual entry"
            elif not device_matches(device, self.s.device_filter):
                reason = f"device '{device or 'unknown'}' not in DEVICE_FILTER"
            self.db.add_activity(aid, detail.get("name", ""), detail.get("sport_type", ""),
                                 detail.get("start_date", ""), device, self.s.targets, reason)
            if not reason:
                summary["new"] += 1

    def _upload(self, summary: dict) -> None:
        targets = [t for t in self.s.targets if t in self.uploaders]
        for row in self.db.work_queue(targets, self.s.max_attempts):
            aid = row["strava_id"]
            todo = [t for t in targets if self.db.needs(row, t, self.s.max_attempts)]
            try:
                workout = self.strava.workout(aid)
                fit_path = self.write_fit(workout)
            except Exception as e:
                for t in todo:
                    self._fail(summary, aid, t, row["name"], f"could not read from Strava: {e}")
                continue
            for t in todo:
                try:
                    status, detail = self.uploaders[t](workout, fit_path)
                    self.db.set_status(aid, t, D.DUPLICATE if status == "duplicate" else D.DONE,
                                       detail, count_attempt=True)
                    summary["uploaded"] += 1
                except Exception as e:
                    log.exception("Upload of %s to %s failed", aid, t)
                    self._fail(summary, aid, t, row["name"], str(e))

    def _fail(self, summary: dict, aid: int, target: str, name: str, err: str) -> None:
        self.db.set_status(aid, target, D.FAILED, err[:500], count_attempt=True)
        summary["failed"] += 1
        summary["errors"].append(f"{target}: '{name}' ({aid}): {err[:300]}")

    def write_fit(self, workout: Workout) -> Path:
        self.fit_dir.mkdir(parents=True, exist_ok=True)
        path = self.fit_dir / f"{workout.id}.fit"
        path.write_bytes(build_fit(workout, spoof_garmin=self.s.garmin_spoof_device,
                                   garmin_product=self.s.garmin_spoof_product))
        return path


def utc_date(s: str, end: bool = False) -> datetime:
    """'2026-10-01' -> start (or end) of that day, UTC."""
    d = datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return d.replace(hour=23, minute=59, second=59) if end else d
