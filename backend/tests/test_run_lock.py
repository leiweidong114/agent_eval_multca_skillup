from __future__ import annotations

import threading
import time

from agent_eval.run_lock import AgentRunLock, agent_run_lock


def test_only_openclaw_family_uses_cross_process_lock(tmp_path):
    assert agent_run_lock(tmp_path, "codex") is None
    assert agent_run_lock(tmp_path, "openclaw") is not None


def test_agent_run_lock_serializes_concurrent_runs(tmp_path):
    path = tmp_path / "runtime.lock"
    first = AgentRunLock(path)
    second = AgentRunLock(path)
    first.acquire()
    acquired = threading.Event()

    worker = threading.Thread(target=lambda: (second.acquire(), acquired.set()))
    worker.start()
    time.sleep(0.1)
    assert acquired.is_set() is False

    first.release()
    assert acquired.wait(2)
    second.release()
    worker.join(timeout=2)
    assert path.exists() is False
