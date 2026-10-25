"""Web UI, /sync endpoint and the schedule."""
from __future__ import annotations

import logging
import secrets
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates

from . import db as D
from .config import Settings
from .garmin import Garmin
from .model import Workout
from .polar import Polar
from .strava import Strava
from .sync import Syncer, utc_date

log = logging.getLogger(__name__)
POLAR_CREDS = "polar_credentials"


def _redirect(msg: str = "") -> RedirectResponse:
    return RedirectResponse("/" + (f"?msg={quote(msg)}" if msg else ""), status_code=303)


def create_app(settings: Settings, *, strava: Strava | None = None, garmin: Garmin | None = None,
               start_scheduler: bool = True) -> FastAPI:
    db = D.DB(settings.data_dir / "workout-sync.db", settings.secret_key)
    strava = strava or Strava(db, settings.strava_client_id, settings.strava_client_secret,
                              f"{settings.public_url}/strava/callback")
    garmin = garmin or Garmin(settings.data_dir / "garmin")

    def upload_garmin(w: Workout, fit_path: Path):
        return garmin.upload(fit_path)

    def upload_polar(w: Workout, fit_path: Path):
        creds = db.get_secret(POLAR_CREDS)
        if not creds:
            raise RuntimeError("Polar is not connected")
        p = Polar()
        p.login(creds["email"], creds["password"])
        return p.upload(w)

    syncer = Syncer(settings, db, strava, {"garmin": upload_garmin, "polar": upload_polar})

    templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
    basic = HTTPBasic(auto_error=False)

    def auth(creds: HTTPBasicCredentials | None = Depends(basic)):
        if creds and secrets.compare_digest(creds.password.encode(), settings.admin_password.encode()):
            return
        raise HTTPException(401, "Login required", headers={"WWW-Authenticate": 'Basic realm="workout-sync"'})

    scheduler = BackgroundScheduler(timezone=settings.timezone)
    for t in settings.sync_times:
        hh, mm = t.split(":")
        scheduler.add_job(lambda: syncer.run("schedule"), CronTrigger(hour=int(hh), minute=int(mm),
                                                                       timezone=settings.timezone))

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if start_scheduler:
            scheduler.start()
        yield
        if scheduler.running:
            scheduler.shutdown(wait=False)

    app = FastAPI(title="workout-sync", docs_url=None, redoc_url=None, lifespan=lifespan)
    app.state.db, app.state.syncer = db, syncer

    @app.get("/health")
    def health():
        return {"ok": True}

    @app.post("/sync")
    def sync(request: Request, token: str = "", creds: HTTPBasicCredentials | None = Depends(basic)):
        token = token or request.headers.get("X-Sync-Token", "")
        ok_token = token and secrets.compare_digest(token.encode(), settings.sync_token.encode())
        ok_basic = creds and secrets.compare_digest(creds.password.encode(), settings.admin_password.encode())
        if not (ok_token or ok_basic):
            raise HTTPException(401, "Bad or missing token")
        started = syncer.start_background("url")
        if "text/html" in request.headers.get("accept", ""):
            return _redirect("Sync started" if started else "A sync is already running")
        return JSONResponse({"status": "started" if started else "busy"}, status_code=202 if started else 409)

    @app.get("/", dependencies=[Depends(auth)])
    def index(request: Request, msg: str = ""):
        jobs = scheduler.get_jobs() if scheduler.running else []
        next_run = min((j.next_run_time for j in jobs if j.next_run_time), default=None)
        since = db.get("sync_since")
        return templates.TemplateResponse(request, "index.html", {
            "msg": msg, "s": settings, "rows": db.recent(100), "last": db.get("last_run"),
            "running": syncer.running, "next_run": next_run,
            "since": datetime.fromtimestamp(since).strftime("%Y-%m-%d %H:%M") if since else None,
            "strava": strava, "garmin": garmin, "polar_email": (db.get_secret(POLAR_CREDS) or {}).get("email"),
            "fmt_ts": lambda ts: datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M") if ts else "",
        })

    @app.post("/sync-now", dependencies=[Depends(auth)])
    def sync_now():
        return _redirect("Sync started" if syncer.start_background("button") else "A sync is already running")

    @app.post("/backfill", dependencies=[Depends(auth)])
    def backfill(date_from: str = Form(...), date_to: str = Form(...)):
        try:
            rng = (utc_date(date_from), utc_date(date_to, end=True))
        except ValueError:
            return _redirect("Dates must look like 2026-10-01")
        started = syncer.start_background("backfill", backfill=rng)
        return _redirect(f"Importing {date_from} to {date_to}" if started else "A sync is already running")

    @app.post("/resend/{strava_id}/{target}", dependencies=[Depends(auth)])
    def resend(strava_id: int, target: str):
        if target not in D.TARGETS or not db.activity(strava_id):
            raise HTTPException(404)
        db.reset(strava_id, target)
        syncer.start_background("resend")
        return _redirect(f"Resending to {target.title()}")

    @app.get("/fit/{strava_id}.fit", dependencies=[Depends(auth)])
    def fit(strava_id: int):
        path = syncer.fit_dir / f"{strava_id}.fit"
        if not path.exists():
            raise HTTPException(404)
        return FileResponse(path, media_type="application/octet-stream", filename=path.name)

    # ---- Strava

    @app.get("/strava/connect", dependencies=[Depends(auth)])
    def strava_connect():
        if not settings.strava_client_id:
            return _redirect("Set STRAVA_CLIENT_ID and STRAVA_CLIENT_SECRET first")
        state = secrets.token_urlsafe(16)
        db.set("strava_state", state)
        return RedirectResponse(strava.authorize_url(state))

    @app.get("/strava/callback", dependencies=[Depends(auth)])
    def strava_callback(code: str = "", state: str = "", scope: str = "", error: str = ""):
        if error or not code:
            return _redirect(f"Strava said: {error or 'no code'}")
        if state != db.get("strava_state"):
            return _redirect("Strava login expired, try again")
        if "activity:read" not in scope:
            return _redirect("Please allow access to your activities on the Strava page")
        strava.exchange_code(code)
        if db.get("sync_since") is None:  # go-live: only activities from now on
            db.set("sync_since", datetime.now().timestamp())
        return _redirect("Strava connected")

    @app.post("/strava/disconnect", dependencies=[Depends(auth)])
    def strava_disconnect():
        strava.disconnect()
        return _redirect("Strava disconnected")

    # ---- Garmin

    @app.post("/garmin/login", dependencies=[Depends(auth)])
    def garmin_login(email: str = Form(...), password: str = Form(...)):
        try:
            status = garmin.login(email, password)
        except Exception as e:
            log.exception("Garmin login failed")
            return _redirect(f"Garmin login failed: {e}")
        return _redirect("Enter the code Garmin sent you" if status == "needs_mfa" else "Garmin connected")

    @app.post("/garmin/mfa", dependencies=[Depends(auth)])
    def garmin_mfa(code: str = Form(...)):
        try:
            garmin.finish_mfa(code)
        except Exception as e:
            log.exception("Garmin MFA failed")
            return _redirect(f"Garmin code failed: {e}")
        return _redirect("Garmin connected")

    @app.post("/garmin/logout", dependencies=[Depends(auth)])
    def garmin_logout():
        garmin.logout()
        return _redirect("Garmin disconnected")

    # ---- Polar

    @app.post("/polar/login", dependencies=[Depends(auth)])
    def polar_login(email: str = Form(...), password: str = Form(...)):
        try:
            Polar().login(email, password)
        except Exception as e:
            return _redirect(f"Polar login failed: {e}")
        db.set_secret(POLAR_CREDS, {"email": email, "password": password})
        return _redirect("Polar connected")

    @app.post("/polar/logout", dependencies=[Depends(auth)])
    def polar_logout():
        db.delete(POLAR_CREDS)
        return _redirect("Polar disconnected")

    return app
