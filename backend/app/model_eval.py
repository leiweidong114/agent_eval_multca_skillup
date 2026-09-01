from __future__ import annotations

import os
from pathlib import Path

from maeval.webapp.api import create_app

from app.config import BACKEND_ROOT


def _data_root() -> Path:
    configured = os.environ.get("MODEL_AGENT_EVAL_DATA_DIR")
    return Path(configured).expanduser().resolve() if configured else BACKEND_ROOT / "model_eval_data"


# Prism Eval runs as a self-contained sub-application. Its SQLite database,
# encrypted provider secrets, benchmark snapshots, and result evidence stay
# separate from Skill-Up artifacts while sharing the same HTTP service.
model_eval_app = create_app(data_dir=_data_root(), trusted_local=True)
