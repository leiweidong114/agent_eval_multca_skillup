from __future__ import annotations

import os
from pathlib import Path


def filesystem_path(path: Path) -> Path:
    """Use Windows extended-length syntax for deep Skill file operations.

    Keep user-facing paths unchanged; this is only for Python filesystem calls.
    The prefix does not make third-party executables long-path-aware, so run
    output should still use a short AGENT_EVAL_RESULTS_ROOT when necessary.
    """
    if os.name != "nt":
        return path
    value = os.path.abspath(os.fspath(path))
    if value.startswith("\\\\?\\"):
        return Path(value)
    if value.startswith("\\\\"):
        return Path("\\\\?\\UNC\\" + value[2:])
    return Path("\\\\?\\" + value)
