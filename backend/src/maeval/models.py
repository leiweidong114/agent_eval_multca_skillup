from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Candidate:
    id: str
    adapter: str
    model: str
    base_url: str | None = None
    api_key_env: str | None = None
    api_key: str | None = field(default=None, repr=False, compare=False)
    timeout_seconds: int = 180
    max_tokens: int = 4096
    temperature: float | None = None
    thinking_budget_tokens: int | None = None
    switch_model: bool = False
    extra_args: tuple[str, ...] = ()
    command: tuple[str, ...] = ()


@dataclass(frozen=True)
class ScorerSpec:
    type: str
    expected: Any = None
    command: tuple[str, ...] = ()
    timeout_seconds: int = 60


@dataclass(frozen=True)
class Task:
    id: str
    kind: str
    prompt: str
    scorer: ScorerSpec
    fixture: Path | None = None
    system: str | None = None
    tags: tuple[str, ...] = ()
    max_tokens: int | None = None


@dataclass
class AdapterResult:
    ok: bool
    text: str = ""
    raw: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    duration_api_ms: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_tokens: int | None = None
    cost_usd: float | None = None
    actual_model: str | None = None


@dataclass
class ScoreResult:
    passed: bool
    score: float
    detail: str
    test_duration_ms: int | None = None


@dataclass
class RunRecord:
    run_id: str
    experiment_id: str
    candidate_id: str
    adapter: str
    requested_model: str
    actual_model: str | None
    task_id: str
    task_kind: str
    tags: list[str]
    repeat: int
    started_at: str
    wall_duration_ms: int
    adapter_ok: bool
    passed: bool
    score: float
    score_detail: str
    response_text: str
    error: str | None
    duration_api_ms: int | None
    test_duration_ms: int | None
    input_tokens: int | None
    output_tokens: int | None
    cache_read_tokens: int | None
    cost_usd: float | None
    changed_files: list[str]
    workdir: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
