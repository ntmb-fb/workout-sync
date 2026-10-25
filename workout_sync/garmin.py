"""Garmin Connect upload through python-garminconnect (unofficial)."""
from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any

from garminconnect import Garmin as GarminClient
from garminconnect import GarminConnectConnectionError

log = logging.getLogger(__name__)


class GarminError(RuntimeError):
    pass


class Garmin:
    """Keeps only OAuth tokens on disk (in token_dir); the password is used once at login."""

    def __init__(self, token_dir: Path):
        self.token_dir = token_dir
        self._lock = threading.Lock()
        self._mfa: tuple[GarminClient, Any] | None = None

    @property
    def connected(self) -> bool:
        return self.token_dir.exists() and any(self.token_dir.iterdir())

    @property
    def waiting_for_mfa(self) -> bool:
        return self._mfa is not None

    def login(self, email: str, password: str) -> str:
        """Returns "ok" or "needs_mfa" (then call finish_mfa with the code)."""
        with self._lock:
            client = GarminClient(email, password, return_on_mfa=True)
            status, state = client.login()
            if status == "needs_mfa":
                self._mfa = (client, state)
                return "needs_mfa"
            self._save(client)
            return "ok"

    def finish_mfa(self, code: str) -> None:
        with self._lock:
            if not self._mfa:
                raise GarminError("No Garmin login is waiting for a code")
            client, state = self._mfa
            client.resume_login(state, code.strip())
            self._mfa = None
            self._save(client)

    def logout(self) -> None:
        with self._lock:
            for f in self.token_dir.glob("*"):
                f.unlink()
            self._mfa = None

    def _save(self, client: GarminClient) -> None:
        self.token_dir.mkdir(parents=True, exist_ok=True)
        client.client.dump(str(self.token_dir))

    def upload(self, fit_path: Path) -> tuple[str, str]:
        """Returns ("done", detail) or ("duplicate", "")."""
        if not self.connected:
            raise GarminError("Garmin is not connected")
        with self._lock:
            client = GarminClient()
            client.login(str(self.token_dir))  # refreshes and re-saves tokens as needed
            try:
                # An import (not a device upload) is not forwarded to Strava, so no loop.
                result = client.import_activity(str(fit_path))
            except GarminConnectConnectionError as e:
                if "duplicate" in str(e).lower():
                    return "duplicate", ""
                raise
        res = (result or {}).get("detailedImportResult", result) or {}
        fails = res.get("failures") or []
        if fails:
            msgs = [m.get("content", "") for f in fails for m in f.get("messages", [])]
            if any("duplicate" in m.lower() for m in msgs):
                return "duplicate", ""
            raise GarminError(f"Garmin rejected the file: {'; '.join(msgs) or fails}")
        ids = [str(s.get("internalId")) for s in res.get("successes") or [] if s.get("internalId")]
        return "done", f"activity {ids[0]}" if ids else ""
