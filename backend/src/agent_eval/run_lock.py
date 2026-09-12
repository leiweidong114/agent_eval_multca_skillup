from __future__ import annotations

import json
import os
import ctypes
import time
import uuid
from pathlib import Path
from threading import Event


class AgentRunLock:
    """Small cross-process lock for runtimes that share a resident Agent service."""

    def __init__(self, path: Path, *, cancel_event: Event | None = None) -> None:
        self.path = path
        self.cancel_event = cancel_event
        self.token = uuid.uuid4().hex
        self.acquired = False

    @staticmethod
    def _process_exists(pid: int) -> bool:
        if pid <= 0:
            return False
        if os.name == "nt":
            process_query_limited_information = 0x1000
            still_active = 259
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(
                process_query_limited_information, False, pid
            )
            if not handle:
                return False
            try:
                exit_code = ctypes.c_ulong()
                return bool(
                    kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
                    and exit_code.value == still_active
                )
            finally:
                kernel32.CloseHandle(handle)
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True

    def _remove_stale(self) -> None:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            pid = int(payload.get("pid") or 0)
            created_at = float(payload.get("created_at") or 0)
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            pid, created_at = 0, 0
        if self._process_exists(pid) and time.time() - created_at < 86400:
            return
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            {"pid": os.getpid(), "created_at": time.time(), "token": self.token}
        ).encode("utf-8")
        while True:
            if self.cancel_event is not None and self.cancel_event.is_set():
                raise InterruptedError("Evaluation cancelled while waiting for Agent run lock")
            try:
                handle = os.open(
                    self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY
                )
            except FileExistsError:
                self._remove_stale()
                time.sleep(0.25)
                continue
            try:
                os.write(handle, payload)
            finally:
                os.close(handle)
            self.acquired = True
            return

    def release(self) -> None:
        if not self.acquired:
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if payload.get("token") == self.token:
                self.path.unlink()
        except FileNotFoundError:
            pass
        finally:
            self.acquired = False


def agent_run_lock(
    project_root: Path, backend_agent: str, *, cancel_event: Event | None = None
) -> AgentRunLock | None:
    if backend_agent != "openclaw":
        return None
    return AgentRunLock(
        project_root / ".runtime" / "locks" / "openclaw-family.lock",
        cancel_event=cancel_event,
    )
