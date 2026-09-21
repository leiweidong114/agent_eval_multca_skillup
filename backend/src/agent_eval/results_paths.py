from __future__ import annotations

import os
from pathlib import Path

from agent_eval.env_config import effective_environment, repository_root


def evaluation_results_root(project_root: Path) -> Path:
    """Return the shared CLI/Web result root, honoring the root .env file.

    A short absolute location is useful on Windows: bundled Skills are copied
    below each run and can otherwise exceed the legacy MAX_PATH limit.
    """
    configured = effective_environment(project_root).get("AGENT_EVAL_RESULTS_ROOT", "").strip()
    if not configured:
        return project_root / "evaluation_results"
    candidate = Path(os.path.expandvars(configured)).expanduser()
    if not candidate.is_absolute():
        candidate = repository_root(project_root) / candidate
    return candidate.resolve()
