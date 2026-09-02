from __future__ import annotations

import csv
import json
import shutil
import threading
import time
import tempfile
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from maeval.adapters import get_adapter
from maeval.models import Candidate, ScorerSpec, Task
from maeval.scoring import score_result

from .db import Database, utcnow
from .reference_agent import run_reference_agent
from .security import SecretBox


KIND_TO_ADAPTER = {
    "anthropic": "direct_http",
    "openai": "openai_http",
    "claude_code_direct": "claude_cli_direct",
    "claude_code_agent": "claude_cli_agent",
    "openclaw_direct": "openclaw_direct",
    "openclaw_agent": "openclaw_agent",
    "codex_direct": "codex_cli_direct",
    "codex_agent": "codex_cli_agent",
    "custom_cli_agent": "custom_cli_agent",
    "custom_http_agent": "custom_http_agent",
}


class EvaluationManager:
    def __init__(self, db: Database, secrets: SecretBox, workspace: Path) -> None:
        self.db, self.secrets, self.workspace = db, secrets, workspace
        self.artifacts_root = db.path.parent / "evaluation-results"
        self._threads: dict[int, threading.Thread] = {}
        self._artifact_locks: dict[int, threading.Lock] = {}
        self._artifact_locks_guard = threading.Lock()

    @staticmethod
    def _atomic_text(path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(path)

    def _artifact_dir(self, experiment_id: int) -> Path:
        relative = f"evaluation-results/experiment-{experiment_id:06d}"
        self.db.execute(
            "UPDATE experiments SET result_dir=COALESCE(result_dir,?) WHERE id=?",
            (relative, experiment_id),
        )
        path = self.db.path.parent / relative.removeprefix("evaluation-results/")
        # db.path.parent is data/, while result_dir is intentionally data-relative.
        path = self.artifacts_root / f"experiment-{experiment_id:06d}"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _artifact_lock(self, experiment_id: int) -> threading.Lock:
        """Return a per-experiment lock for concurrent append-only evidence files."""
        with self._artifact_locks_guard:
            return self._artifact_locks.setdefault(experiment_id, threading.Lock())

    @staticmethod
    def _sanitized(value: Any) -> Any:
        """Remove credentials from arbitrary adapter payloads before persisting them."""
        sensitive_fragments = (
            "api_key", "authorization", "password", "secret", "credential",
            "access_token", "refresh_token", "cookie",
        )
        if isinstance(value, dict):
            cleaned: dict[str, Any] = {}
            for key, item in value.items():
                key_text = str(key)
                if any(fragment in key_text.lower() for fragment in sensitive_fragments):
                    cleaned[key_text] = "[REDACTED]"
                else:
                    cleaned[key_text] = EvaluationManager._sanitized(item)
            return cleaned
        if isinstance(value, (list, tuple)):
            return [EvaluationManager._sanitized(item) for item in value]
        if isinstance(value, Path):
            return str(value)
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        return repr(value)

    @staticmethod
    def _decoded(value: Any, default: Any = None) -> Any:
        if value in (None, ""):
            return default
        if not isinstance(value, str):
            return value
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return value

    def _append_artifact_line(self, experiment_id: int, filename: str, line: str) -> None:
        path = self._artifact_dir(experiment_id) / filename
        with self._artifact_lock(experiment_id):
            with path.open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(line.rstrip("\r\n") + "\n")
                stream.flush()

    def _log_execution(
        self,
        experiment_id: int,
        event: str,
        *,
        level: str = "INFO",
        **details: Any,
    ) -> None:
        suffix = json.dumps(
            self._sanitized(details), ensure_ascii=False, separators=(",", ":")
        )
        self._append_artifact_line(
            experiment_id,
            "execution.log",
            f"{utcnow()} {level.upper()} {event} {suffix}",
        )

    def _experiment_artifact(self, experiment_id: int) -> dict[str, Any]:
        experiment = self.db.row("SELECT * FROM experiments WHERE id=?", (experiment_id,)) or {}
        for field in ("provider_ids_json", "benchmark_ids_json", "budget_json", "manifest_json"):
            if field in experiment:
                experiment[field.removesuffix("_json")] = json.loads(experiment.pop(field) or "{}")
        return experiment

    def _write_manifest_artifact(self, experiment_id: int) -> None:
        folder = self._artifact_dir(experiment_id)
        experiment = self._experiment_artifact(experiment_id)
        payload = {
            "experiment_id": experiment_id,
            "created_at": experiment.get("created_at"),
            "config_hash": experiment.get("config_hash"),
            "manifest": experiment.get("manifest", {}),
            "artifacts": {
                "execution_log": "execution.log",
                "input_output_answers": "evaluation-io.jsonl",
                "per_item_results": "items/",
                "final_results": "results.jsonl",
            },
        }
        self._atomic_text(
            folder / "manifest.json",
            json.dumps(payload, ensure_ascii=False, indent=2),
        )
        # Create the stream up front so even a zero-job or early-failed run has a
        # complete, predictable artifact layout. Never truncate recovery data.
        with self._artifact_lock(experiment_id):
            (folder / "evaluation-io.jsonl").touch(exist_ok=True)
        self._log_execution(experiment_id, "manifest.written", path="manifest.json")

    def _result_rows(self, experiment_id: int) -> list[dict[str, Any]]:
        return self.db.rows(
            """SELECT r.*,p.name provider_name,p.kind provider_kind,p.model,
            b.name benchmark_name,bi.benchmark_id,bi.item_key,bi.category,
            bi.prompt,bi.expected_json,bi.scorer_type
            FROM results r JOIN providers p ON p.id=r.provider_id
            JOIN benchmark_items bi ON bi.id=r.benchmark_item_id
            JOIN benchmarks b ON b.id=bi.benchmark_id
            WHERE r.experiment_id=? ORDER BY r.id""",
            (experiment_id,),
        )

    def _write_result_artifact(self, result_id: int) -> None:
        row = self.db.row(
            """SELECT r.*,p.name provider_name,p.kind provider_kind,p.model,
            b.name benchmark_name,bi.benchmark_id,bi.item_key,bi.category,
            bi.prompt,bi.expected_json,bi.scorer_type
            FROM results r JOIN providers p ON p.id=r.provider_id
            JOIN benchmark_items bi ON bi.id=r.benchmark_item_id
            JOIN benchmarks b ON b.id=bi.benchmark_id WHERE r.id=?""",
            (result_id,),
        )
        if not row:
            return
        folder = self._artifact_dir(int(row["experiment_id"])) / "items"
        self._atomic_text(
            folder / f"result-{result_id:08d}.json",
            json.dumps(row, ensure_ascii=False, indent=2),
        )

    def _write_io_artifact(self, result_id: int, result: Any, scored: Any) -> None:
        row = self.db.row(
            """SELECT r.*,e.track,e.config_hash,p.name provider_name,p.kind provider_kind,
            p.model requested_model,p.base_url,p.settings_json,b.name benchmark_name,
            bi.benchmark_id,bi.item_key,bi.category,bi.prompt,bi.expected_json,
            bi.scorer_type,bi.metadata_json
            FROM results r JOIN experiments e ON e.id=r.experiment_id
            JOIN providers p ON p.id=r.provider_id
            JOIN benchmark_items bi ON bi.id=r.benchmark_item_id
            JOIN benchmarks b ON b.id=bi.benchmark_id WHERE r.id=?""",
            (result_id,),
        )
        if not row:
            return
        metadata = self._decoded(row.pop("metadata_json", None), {})
        settings = self._decoded(row.pop("settings_json", None), {})
        expected = self._decoded(row.pop("expected_json", None))
        raw = self._sanitized(result.raw if result else {})
        record = {
            "schema_version": "prism-evaluation-io-v1",
            "recorded_at": utcnow(),
            "experiment": {
                "id": row["experiment_id"],
                "track": row.get("track"),
                "config_hash": row.get("config_hash"),
            },
            "result_id": result_id,
            "provider": {
                "id": row["provider_id"],
                "name": row.get("provider_name"),
                "kind": row.get("provider_kind"),
                "base_url": row.get("base_url"),
                "requested_model": row.get("requested_model"),
                "actual_model": row.get("actual_model"),
                "settings": self._sanitized(settings),
            },
            "benchmark": {
                "id": row.get("benchmark_id"),
                "name": row.get("benchmark_name"),
                "item_id": row.get("benchmark_item_id"),
                "item_key": row.get("item_key"),
                "category": row.get("category"),
                "repeat": row.get("repeat"),
            },
            "input": {
                "system": None,
                "prompt": row.get("prompt"),
                "task_kind": metadata.get("kind", "direct") if isinstance(metadata, dict) else "direct",
                "scorer_type": row.get("scorer_type"),
                "metadata": self._sanitized(metadata),
            },
            "expected_answer": expected,
            "output": {
                "ok": bool(result.ok) if result else False,
                "text": row.get("response_text") or "",
                "error": row.get("error"),
                "raw_execution": raw,
            },
            "score": {
                "passed": bool(row.get("passed")),
                "value": row.get("score"),
                "detail": row.get("detail"),
            },
            "execution": {
                "status": row.get("status"),
                "wall_duration_ms": row.get("wall_duration_ms"),
                "duration_api_ms": row.get("duration_api_ms"),
                "test_duration_ms": getattr(scored, "test_duration_ms", None) if scored else None,
                "input_tokens": row.get("input_tokens"),
                "output_tokens": row.get("output_tokens"),
                "cache_read_tokens": getattr(result, "cache_read_tokens", None) if result else None,
                "cost_usd": row.get("cost_usd"),
            },
        }
        self._append_artifact_line(
            int(row["experiment_id"]),
            "evaluation-io.jsonl",
            json.dumps(record, ensure_ascii=False, separators=(",", ":")),
        )

    def _log_adapter_trace(self, experiment_id: int, result_id: int, result: Any) -> None:
        if not result or not result.raw:
            return
        raw = self._sanitized(result.raw)
        event_lists: list[tuple[str, list[Any]]] = []

        def collect(value: Any, path: str = "raw_execution") -> None:
            if isinstance(value, dict):
                for key, child in value.items():
                    child_path = f"{path}.{key}"
                    if key == "events" and isinstance(child, list):
                        event_lists.append((child_path, child))
                    else:
                        collect(child, child_path)
            elif isinstance(value, list):
                for index, child in enumerate(value):
                    collect(child, f"{path}[{index}]")

        collect(raw)
        for path, events in event_lists:
            for index, event in enumerate(events, start=1):
                self._log_execution(
                    experiment_id,
                    "agent.step",
                    result_id=result_id,
                    trace_path=path,
                    step=index,
                    data=event,
                )
        reference = raw.get("reference_agent") if isinstance(raw, dict) else None
        if reference:
            self._log_execution(
                experiment_id,
                "reference_agent.summary",
                result_id=result_id,
                data=reference,
            )

    def _finalize_artifacts(self, experiment_id: int) -> None:
        folder = self._artifact_dir(experiment_id)
        experiment = self._experiment_artifact(experiment_id)
        rows = self._result_rows(experiment_id)
        self._atomic_text(
            folder / "experiment.json",
            json.dumps(experiment, ensure_ascii=False, indent=2),
        )
        self._atomic_text(
            folder / "results.jsonl",
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        )
        csv_path = folder / "results.csv"
        temporary = csv_path.with_suffix(".csv.tmp")
        temporary.parent.mkdir(parents=True, exist_ok=True)
        with temporary.open("w", encoding="utf-8-sig", newline="") as stream:
            fields = list(rows[0]) if rows else ["experiment_id"]
            writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
        temporary.replace(csv_path)
        totals = self.db.row(
            """SELECT COUNT(*) result_count,COALESCE(SUM(passed),0) passed_count,
            COALESCE(AVG(score),0) average_score,COALESCE(SUM(input_tokens),0) input_tokens,
            COALESCE(SUM(output_tokens),0) output_tokens,COALESCE(SUM(cost_usd),0) cost_usd
            FROM results WHERE experiment_id=?""",
            (experiment_id,),
        ) or {}
        self._atomic_text(
            folder / "summary.json",
            json.dumps({"experiment": experiment, "totals": totals}, ensure_ascii=False, indent=2),
        )

    def start(self, experiment_id: int) -> None:
        thread = threading.Thread(target=self._run, args=(experiment_id,), daemon=True)
        self._threads[experiment_id] = thread
        thread.start()

    def preview(
        self,
        provider: dict[str, Any],
        item: dict[str, Any],
        allow_unsafe: bool = False,
    ) -> dict[str, Any]:
        """Run one visible item without creating an experiment or result row."""
        if item["scorer_type"] in {"humaneval", "mbpp"} and not allow_unsafe:
            raise ValueError("generated-code execution was not authorized")
        expected = json.loads(item["expected_json"]) if item["expected_json"] else None
        metadata = json.loads(item["metadata_json"] or "{}")
        settings = json.loads(provider["settings_json"] or "{}")
        candidate = Candidate(
            id=str(provider["id"]),
            adapter=KIND_TO_ADAPTER[provider["kind"]],
            model=provider["model"],
            base_url=provider["base_url"],
            api_key=self.secrets.decrypt(provider["api_key_cipher"]),
            timeout_seconds=min(int(settings.get("timeout_seconds", 180)), 300),
            max_tokens=min(int(settings.get("max_tokens", 2048)), 4096),
            temperature=settings.get("temperature"),
            switch_model=bool(settings.get("switch_model", False)),
            extra_args=tuple(settings.get("extra_args", [])),
            command=tuple(settings.get("command", [])),
        )
        task_kind = metadata.get("kind", "direct")
        if candidate.adapter.endswith("_agent") != (task_kind == "repo"):
            raise ValueError(
                "direct providers are required for QA items; agent providers are required for repository items"
            )
        temporary: tempfile.TemporaryDirectory[str] | None = None
        workdir = None
        fixture = None
        try:
            if task_kind == "repo":
                temporary = tempfile.TemporaryDirectory(prefix="prism-preview-")
                workdir = Path(temporary.name)
                source = self.workspace / metadata["fixture"]
                shutil.copytree(source, workdir, dirs_exist_ok=True)
                fixture = workdir
            task = Task(
                id=item["item_key"],
                kind=task_kind,
                prompt=item["prompt"],
                fixture=fixture,
                scorer=ScorerSpec(
                    type=item["scorer_type"],
                    expected=expected,
                    command=tuple(metadata.get("command", [])),
                    timeout_seconds=int(settings.get("score_timeout_seconds", 30)),
                ),
                tags=(item["benchmark_id"], item["category"] or ""),
            )
            started = time.perf_counter()
            result = get_adapter(candidate.adapter).run(candidate, task, workdir)
            self._estimate_cost(result, settings)
            scored = score_result(task, result, workdir)
            return {
                "ok": result.ok,
                "passed": scored.passed,
                "score": scored.score,
                "prompt": item["prompt"],
                "expected": expected,
                "scorer_type": item["scorer_type"],
                "response_text": result.text,
                "error": result.error,
                "detail": scored.detail,
                "wall_duration_ms": int((time.perf_counter() - started) * 1000),
                "duration_api_ms": result.duration_api_ms,
                "input_tokens": result.input_tokens,
                "output_tokens": result.output_tokens,
                "cost_usd": result.cost_usd,
                "actual_model": result.actual_model,
            }
        finally:
            if temporary:
                temporary.cleanup()

    @staticmethod
    def _estimate_cost(result: Any, settings: dict[str, Any]) -> None:
        if result is None or result.cost_usd is not None:
            return
        if result.input_tokens is None and result.output_tokens is None:
            return
        input_rate = float(settings.get("input_cost_per_million", 0) or 0)
        output_rate = float(settings.get("output_cost_per_million", 0) or 0)
        if not input_rate and not output_rate:
            return
        result.cost_usd = (
            (result.input_tokens or 0) * input_rate
            + (result.output_tokens or 0) * output_rate
        ) / 1_000_000

    def _run(self, experiment_id: int) -> None:
        exp = self.db.row("SELECT * FROM experiments WHERE id=?", (experiment_id,))
        if not exp:
            return
        self._write_manifest_artifact(experiment_id)
        provider_ids = json.loads(exp["provider_ids_json"])
        placeholders_p = ",".join("?" for _ in provider_ids)
        providers = self.db.rows(f"SELECT * FROM providers WHERE id IN ({placeholders_p})", tuple(provider_ids))
        items = self.db.rows(
            """SELECT bi.* FROM experiment_items ei
            JOIN benchmark_items bi ON bi.id=ei.benchmark_item_id
            WHERE ei.experiment_id=? ORDER BY ei.selection_order""",
            (experiment_id,),
        )
        # Compatibility for experiments created before protocol snapshots existed.
        if not items:
            benchmark_ids = json.loads(exp["benchmark_ids_json"])
            for benchmark_id in benchmark_ids:
                items.extend(
                    self.db.rows(
                        "SELECT * FROM benchmark_items WHERE benchmark_id=? ORDER BY id",
                        (benchmark_id,),
                    )[: exp["sample_limit"]]
                    if exp["sample_limit"]
                    else self.db.rows(
                        "SELECT * FROM benchmark_items WHERE benchmark_id=? ORDER BY id",
                        (benchmark_id,),
                    )
                )
        jobs = [(p, item, repeat) for p in providers for item in items for repeat in range(1, exp["repeats"] + 1)]
        self.db.execute("UPDATE experiments SET status='running',started_at=?,total_jobs=? WHERE id=?",
                        (utcnow(), len(jobs), experiment_id))
        self._log_execution(
            experiment_id,
            "experiment.started",
            track=exp.get("track"),
            provider_count=len(providers),
            item_count=len(items),
            repeat_count=exp["repeats"],
            total_jobs=len(jobs),
            concurrency=exp["concurrency"],
        )
        try:
            with ThreadPoolExecutor(max_workers=exp["concurrency"]) as pool:
                futures = [pool.submit(self._one, experiment_id, p, item, repeat, bool(exp["allow_unsafe_code"]))
                           for p, item, repeat in jobs]
                for future in as_completed(futures):
                    future.result()
                    state = self.db.row("SELECT cancel_requested FROM experiments WHERE id=?", (experiment_id,))
                    if state and state["cancel_requested"]:
                        for pending in futures:
                            pending.cancel()
                        break
            state = self.db.row("SELECT cancel_requested FROM experiments WHERE id=?", (experiment_id,))
            status = "cancelled" if state and state["cancel_requested"] else "completed"
            self.db.execute("UPDATE experiments SET status=?,finished_at=? WHERE id=?",
                            (status, utcnow(), experiment_id))
            self._log_execution(experiment_id, f"experiment.{status}")
        except Exception as exc:
            self.db.execute("UPDATE experiments SET status='failed',error=?,finished_at=? WHERE id=?",
                            (str(exc), utcnow(), experiment_id))
            self._log_execution(
                experiment_id,
                "experiment.failed",
                level="ERROR",
                error=str(exc),
                traceback=traceback.format_exc(),
            )
        finally:
            self._finalize_artifacts(experiment_id)
            self._log_execution(experiment_id, "artifacts.finalized")

    def _one(self, experiment_id: int, provider: dict[str, Any], item: dict[str, Any],
             repeat: int, allow_unsafe: bool) -> None:
        state = self.db.row("SELECT cancel_requested FROM experiments WHERE id=?", (experiment_id,))
        if state and state["cancel_requested"]:
            return
        self._log_execution(
            experiment_id,
            "task.started",
            provider_id=provider["id"],
            provider_name=provider.get("name"),
            provider_kind=provider.get("kind"),
            requested_model=provider.get("model"),
            benchmark_id=item.get("benchmark_id"),
            item_id=item.get("id"),
            item_key=item.get("item_key"),
            repeat=repeat,
            prompt_chars=len(item.get("prompt") or ""),
        )
        expected = json.loads(item["expected_json"]) if item["expected_json"] else None
        metadata = json.loads(item["metadata_json"] or "{}")
        if item["scorer_type"] in {"humaneval", "mbpp"} and not allow_unsafe:
            self._save(experiment_id, provider["id"], item["id"], repeat, None, None,
                       "blocked", "Generated-code execution was not authorized", 0)
            return
        settings = json.loads(provider["settings_json"] or "{}")
        experiment = self.db.row(
            "SELECT budget_json,track FROM experiments WHERE id=?", (experiment_id,)
        ) or {}
        budget = json.loads(experiment.get("budget_json") or "{}")
        provider_timeout = int(settings.get("timeout_seconds", 180))
        timeout_budget = int(budget.get("timeout_seconds_per_task", provider_timeout))
        provider_max_tokens = int(settings.get("max_tokens", 2048))
        token_budget = int(budget.get("max_output_tokens", provider_max_tokens))
        candidate = Candidate(
            id=str(provider["id"]), adapter=KIND_TO_ADAPTER[provider["kind"]],
            model=provider["model"], base_url=provider["base_url"],
            api_key=self.secrets.decrypt(provider["api_key_cipher"]),
            timeout_seconds=min(provider_timeout, timeout_budget),
            max_tokens=min(provider_max_tokens, token_budget),
            temperature=settings.get("temperature"),
            switch_model=bool(settings.get("switch_model", False)),
            extra_args=tuple(settings.get("extra_args", [])),
            command=tuple(settings.get("command", [])),
        )
        task_kind = metadata.get("kind", "direct")
        workdir = None
        fixture = None
        if task_kind == "repo":
            fixture = self.workspace / metadata["fixture"]
            workdir = self.workspace / "data" / "workspaces" / (
                f"exp{experiment_id}-{provider['id']}-{item['item_key']}-{repeat}-{uuid.uuid4().hex[:8]}"
            )
            workdir.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(fixture, workdir)
            fixture = workdir
        task = Task(id=item["item_key"], kind=task_kind, prompt=item["prompt"], fixture=fixture,
                    scorer=ScorerSpec(type=item["scorer_type"], expected=expected,
                                      command=tuple(metadata.get("command", [])),
                                      timeout_seconds=int(settings.get("score_timeout_seconds", 30))),
                    tags=(item["benchmark_id"], item["category"] or ""))
        if experiment.get("track") == "reference_agent":
            if provider["kind"] not in {"anthropic", "openai"} or workdir is None:
                self._save(
                    experiment_id,
                    provider["id"],
                    item["id"],
                    repeat,
                    None,
                    None,
                    "incompatible",
                    "Reference Agent v1 requires an HTTP API model and repository task",
                    0,
                )
                return
            started = time.perf_counter()
            result, changed_files = run_reference_agent(
                candidate,
                task.prompt,
                workdir,
                max_context_chars=int(budget.get("max_context_chars", 60_000)),
                max_files_changed=int(budget.get("max_files_changed", 8)),
            )
            self._estimate_cost(result, settings)
            scored = score_result(task, result, workdir)
            if changed_files:
                scored.detail += f"; reference agent changed: {', '.join(changed_files)}"
            wall = int((time.perf_counter() - started) * 1000)
            status = "completed" if result.ok else ("completed_with_warning" if scored.passed else "error")
            self._save(
                experiment_id,
                provider["id"],
                item["id"],
                repeat,
                result,
                scored,
                status,
                result.error,
                wall,
            )
            return
        if candidate.adapter.endswith("_agent") and task_kind != "repo":
            self._save(experiment_id, provider["id"], item["id"], repeat, None, None, "incompatible",
                       "标准问答题需使用 direct 模式；agent 模式用于仓库任务集", 0)
            return
        if not candidate.adapter.endswith("_agent") and task_kind == "repo":
            self._save(experiment_id, provider["id"], item["id"], repeat, None, None, "incompatible",
                       "仓库任务需使用 Claude Code Agent 或 OpenClaw Agent 模式", 0)
            return
        started = time.perf_counter()
        result = get_adapter(candidate.adapter).run(candidate, task, workdir)
        self._estimate_cost(result, settings)
        scored = score_result(task, result, workdir)
        wall = int((time.perf_counter() - started) * 1000)
        status = (
            "completed"
            if result.ok
            else ("completed_with_warning" if scored.passed else "error")
        )
        self._save(
            experiment_id,
            provider["id"],
            item["id"],
            repeat,
            result,
            scored,
            status,
            result.error,
            wall,
        )

    def _save(self, experiment_id: int, provider_id: int, item_id: int, repeat: int,
              result: Any, scored: Any, status: str, error: str | None, wall: int) -> None:
        passed = int(bool(scored and scored.passed))
        result_id = self.db.execute(
            """INSERT INTO results(experiment_id,provider_id,benchmark_item_id,repeat,status,passed,score,
            response_text,error,wall_duration_ms,duration_api_ms,input_tokens,output_tokens,cost_usd,
            actual_model,detail,created_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (experiment_id, provider_id, item_id, repeat, status, passed,
             float(scored.score) if scored else 0.0, result.text if result else "", error, wall,
             result.duration_api_ms if result else None, result.input_tokens if result else None,
             result.output_tokens if result else None, result.cost_usd if result else None,
             result.actual_model if result else None,
             scored.detail if scored else error, utcnow()),
        )
        self.db.execute(
            "UPDATE experiments SET completed_jobs=completed_jobs+1,passed_jobs=passed_jobs+? WHERE id=?",
            (passed, experiment_id),
        )
        self._write_result_artifact(result_id)
        self._write_io_artifact(result_id, result, scored)
        self._log_adapter_trace(experiment_id, result_id, result)
        self._log_execution(
            experiment_id,
            "task.finished",
            level="ERROR" if status == "error" else "INFO",
            result_id=result_id,
            provider_id=provider_id,
            item_id=item_id,
            repeat=repeat,
            status=status,
            passed=bool(passed),
            score=float(scored.score) if scored else 0.0,
            response_chars=len(result.text) if result else 0,
            wall_duration_ms=wall,
            duration_api_ms=result.duration_api_ms if result else None,
            input_tokens=result.input_tokens if result else None,
            output_tokens=result.output_tokens if result else None,
            cost_usd=result.cost_usd if result else None,
            error=error,
        )
