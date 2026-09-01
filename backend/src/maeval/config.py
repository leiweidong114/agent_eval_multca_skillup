from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .models import Candidate, ScorerSpec, Task


@dataclass(frozen=True)
class Experiment:
    id: str
    source_path: Path
    candidates: tuple[Candidate, ...]
    tasks: tuple[Task, ...]
    repeats: int = 1
    concurrency: int = 1
    output_dir: Path = Path("runs")


def _required(obj: dict[str, Any], key: str, context: str) -> Any:
    if key not in obj:
        raise ValueError(f"{context}: missing required field {key!r}")
    return obj[key]


def load_experiment(path: str | Path) -> Experiment:
    source = Path(path).resolve()
    data = json.loads(source.read_text(encoding="utf-8"))
    root = source.parent

    candidates: list[Candidate] = []
    seen_candidates: set[str] = set()
    for item in _required(data, "candidates", "experiment"):
        candidate_id = str(_required(item, "id", "candidate"))
        if candidate_id in seen_candidates:
            raise ValueError(f"duplicate candidate id: {candidate_id}")
        seen_candidates.add(candidate_id)
        candidates.append(
            Candidate(
                id=candidate_id,
                adapter=str(_required(item, "adapter", candidate_id)),
                model=str(_required(item, "model", candidate_id)),
                base_url=item.get("base_url"),
                api_key_env=item.get("api_key_env"),
                timeout_seconds=int(item.get("timeout_seconds", 180)),
                max_tokens=int(item.get("max_tokens", 4096)),
                temperature=item.get("temperature"),
                thinking_budget_tokens=(
                    int(item["thinking_budget_tokens"])
                    if item.get("thinking_budget_tokens")
                    else None
                ),
                switch_model=bool(item.get("switch_model", False)),
                extra_args=tuple(str(x) for x in item.get("extra_args", [])),
            )
        )

    tasks: list[Task] = []
    seen_tasks: set[str] = set()
    for item in _required(data, "tasks", "experiment"):
        task_id = str(_required(item, "id", "task"))
        if task_id in seen_tasks:
            raise ValueError(f"duplicate task id: {task_id}")
        seen_tasks.add(task_id)
        scorer_data = _required(item, "scorer", task_id)
        fixture_value = item.get("fixture")
        fixture = (root / fixture_value).resolve() if fixture_value else None
        prompt_value = item.get("prompt")
        if prompt_value is None and item.get("prompt_file"):
            prompt_value = (root / item["prompt_file"]).read_text(
                encoding="utf-8"
            )
        if prompt_value is None:
            raise ValueError(f"{task_id}: missing prompt or prompt_file")
        task = Task(
            id=task_id,
            kind=str(_required(item, "kind", task_id)),
            prompt=str(prompt_value),
            system=item.get("system"),
            fixture=fixture,
            tags=tuple(str(x) for x in item.get("tags", [])),
            max_tokens=(
                int(item["max_tokens"]) if item.get("max_tokens") else None
            ),
            scorer=ScorerSpec(
                type=str(_required(scorer_data, "type", f"{task_id}.scorer")),
                expected=scorer_data.get("expected"),
                command=tuple(str(x) for x in scorer_data.get("command", [])),
                timeout_seconds=int(scorer_data.get("timeout_seconds", 60)),
            ),
        )
        if task.kind == "repo" and task.fixture is None:
            raise ValueError(f"{task_id}: repo task requires fixture")
        if task.fixture is not None and not task.fixture.is_dir():
            raise ValueError(f"{task_id}: fixture directory not found: {task.fixture}")
        tasks.append(task)

    output_value = data.get("output_dir", "runs")
    output_dir = (root / output_value).resolve()
    experiment = Experiment(
        id=str(_required(data, "id", "experiment")),
        source_path=source,
        candidates=tuple(candidates),
        tasks=tuple(tasks),
        repeats=max(1, int(data.get("repeats", 1))),
        concurrency=max(1, int(data.get("concurrency", 1))),
        output_dir=output_dir,
    )
    validate_compatibility(experiment)
    return experiment


def validate_compatibility(experiment: Experiment) -> None:
    direct_adapters = {"direct_http", "claude_cli_direct"}
    agent_adapters = {"claude_cli_agent", "openclaw_agent"}
    known = direct_adapters | agent_adapters
    for candidate in experiment.candidates:
        if candidate.adapter not in known:
            raise ValueError(
                f"{candidate.id}: unknown adapter {candidate.adapter!r}; "
                f"expected one of {sorted(known)}"
            )
    for task in experiment.tasks:
        if task.kind not in {"direct", "repo"}:
            raise ValueError(f"{task.id}: unknown task kind {task.kind!r}")
