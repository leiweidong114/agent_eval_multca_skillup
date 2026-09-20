from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent_eval.env_config import effective_environment
from app.config import BACKEND_ROOT


class InfrastructureConfigurationError(RuntimeError):
    """Raised when an external data service is not configured correctly."""


@dataclass(frozen=True)
class InfrastructureSettings:
    schematic_data_api_base_url: str | None
    schematic_data_query_path: str
    schematic_data_write_path: str | None
    schematic_data_api_cookie: str | None
    schematic_data_timeout_seconds: float
    schematic_data_query_page_size: int
    schematic_data_query_max_pages: int
    cache_default_ttl_seconds: int
    cache_max_size: int
    metrics_sqlite_path: Path
    source: str = "environment"


def _positive_int(value: Any, default: int, *, maximum: int | None = None) -> int:
    try:
        parsed = int(str(value))
    except (TypeError, ValueError):
        parsed = default
    parsed = max(1, parsed)
    return min(parsed, maximum) if maximum is not None else parsed


def load_infrastructure_settings(*, force: bool = False) -> InfrastructureSettings:
    """Load local-cache and schematic-data API settings from the project .env."""
    del force
    environment = effective_environment(BACKEND_ROOT)
    base_url = str(environment.get("SCHEMATIC_DATA_API_BASE_URL") or "").strip().rstrip("/")
    query_path = str(
        environment.get("SCHEMATIC_DATA_QUERY_PATH")
        or "/schematic/schematicData/query"
    ).strip()
    write_path = str(
        environment.get("SCHEMATIC_DATA_WRITE_PATH")
        or "/schematic/schematicData/insert"
    ).strip()
    api_cookie = str(environment.get("SCHEMATIC_DATA_API_COOKIE") or "").strip()
    sqlite_value = str(
        environment.get("SESSION_METRICS_SQLITE_PATH")
        or "backend/data/session_metrics.sqlite3"
    ).strip()
    sqlite_path = Path(sqlite_value)
    if not sqlite_path.is_absolute():
        sqlite_path = (BACKEND_ROOT.parent / sqlite_path).resolve()
    try:
        timeout = max(1.0, float(environment.get("SCHEMATIC_DATA_API_TIMEOUT_SECONDS") or 15))
    except (TypeError, ValueError):
        timeout = 15.0
    return InfrastructureSettings(
        schematic_data_api_base_url=base_url or None,
        schematic_data_query_path="/" + query_path.lstrip("/"),
        schematic_data_write_path=("/" + write_path.lstrip("/")) if write_path else None,
        schematic_data_api_cookie=api_cookie or None,
        schematic_data_timeout_seconds=timeout,
        schematic_data_query_page_size=_positive_int(
            environment.get("SCHEMATIC_DATA_QUERY_PAGE_SIZE"), 20, maximum=100
        ),
        schematic_data_query_max_pages=_positive_int(
            environment.get("SCHEMATIC_DATA_QUERY_MAX_PAGES"), 50, maximum=1000
        ),
        cache_default_ttl_seconds=_positive_int(
            environment.get("AGENT_EVAL_CACHE_TTL_SECONDS"), 300
        ),
        cache_max_size=_positive_int(environment.get("AGENT_EVAL_CACHE_MAX_SIZE"), 1000),
        metrics_sqlite_path=sqlite_path,
    )


def infrastructure_health() -> dict[str, Any]:
    """Return non-secret configuration readiness for diagnostics and the UI."""
    try:
        settings = load_infrastructure_settings(force=True)
    except Exception as exc:
        return {"status": "error", "error": str(exc), "data_api": False}
    return {
        "status": "configured" if settings.schematic_data_api_base_url else "not_configured",
        "source": settings.source,
        "data_api": bool(settings.schematic_data_api_base_url),
        "query_path": settings.schematic_data_query_path,
        "write_configured": bool(settings.schematic_data_write_path),
        "cookie_configured": bool(settings.schematic_data_api_cookie),
        "metrics_store": "sqlite",
        "cache": "memory_ttl_lru",
    }
