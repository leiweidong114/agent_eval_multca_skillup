from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .connectivity import test_provider_connection
from .db import Database, utcnow
from .security import SecretBox


class ProviderHealthMonitor:
    def __init__(self, db: Database, secrets: SecretBox, root: Path) -> None:
        self.db, self.secrets, self.root = db, secrets, root
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)

    def _loop(self) -> None:
        while not self._stop.wait(30):
            self.run_due_checks()

    @staticmethod
    def _due(setting: dict[str, Any], now: datetime) -> bool:
        if not setting.get("health_check_enabled"):
            return False
        last = setting.get("last_health_check_at")
        if not last:
            return True
        try:
            observed = datetime.fromisoformat(last)
        except ValueError:
            return True
        return now >= observed + timedelta(
            minutes=int(setting.get("health_check_interval_minutes") or 60)
        )

    def run_due_checks(self) -> int:
        now = datetime.now(timezone.utc)
        checked = 0
        for setting in self.db.rows("SELECT * FROM user_settings"):
            if not self._due(setting, now):
                continue
            for provider in self.db.rows(
                "SELECT * FROM providers WHERE owner_user_id=?", (setting["user_id"],)
            ):
                result = test_provider_connection(provider, self.secrets, self.root)
                self.record(provider, result, "scheduled")
                checked += 1
            self.db.execute(
                "UPDATE user_settings SET last_health_check_at=?,updated_at=? WHERE user_id=?",
                (utcnow(), utcnow(), setting["user_id"]),
            )
        return checked

    def record(
        self, provider: dict[str, Any], result: dict[str, Any], source: str
    ) -> None:
        self.db.execute(
            """INSERT INTO provider_health_checks(
            provider_id,owner_user_id,ok,message,actual_model,duration_ms,source,checked_at)
            VALUES(?,?,?,?,?,?,?,?)""",
            (
                provider["id"],
                provider["owner_user_id"],
                None if result.get("ok") is None else int(bool(result.get("ok"))),
                result.get("message"),
                result.get("actual_model"),
                result.get("duration_ms"),
                source,
                utcnow(),
            ),
        )
