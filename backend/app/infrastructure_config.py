from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any, Mapping

import httpx
import yaml

from agent_eval.env_config import effective_environment
from app.config import BACKEND_ROOT


class InfrastructureConfigurationError(RuntimeError):
    """Raised when a configured infrastructure service cannot be resolved."""


@dataclass(frozen=True)
class InfrastructureSettings:
    mongodb_uri: str | None
    mongodb_database: str
    redis_url: str | None
    source: str


_LOCK = threading.Lock()
_CACHED: tuple[float, InfrastructureSettings] | None = None


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _load_nacos(environment: Mapping[str, str]) -> dict[str, Any]:
    address = str(environment.get("NACOS_SERVER_ADDR") or "").strip().rstrip("/")
    if not address:
        return {}
    if not address.startswith(("http://", "https://")):
        address = "http://" + address
    data_id = str(environment.get("NACOS_DATA_ID") or "agent-eval-infrastructure.yaml")
    group = str(environment.get("NACOS_GROUP") or "AGENT_EVAL")
    namespace = str(environment.get("NACOS_NAMESPACE") or "")
    params: dict[str, str] = {"dataId": data_id, "group": group}
    if namespace:
        params["tenant"] = namespace
    username = str(environment.get("NACOS_USERNAME") or "").strip()
    password = str(environment.get("NACOS_PASSWORD") or "")
    if username:
        response = httpx.post(
            address + "/nacos/v1/auth/login",
            data={"username": username, "password": password},
            timeout=10,
            trust_env=False,
        )
        response.raise_for_status()
        token = response.json().get("accessToken")
        if token:
            params["accessToken"] = str(token)
    response = httpx.get(
        address + "/nacos/v1/cs/configs",
        params=params,
        timeout=10,
        trust_env=False,
    )
    response.raise_for_status()
    value = yaml.safe_load(response.text) or {}
    if not isinstance(value, dict):
        raise InfrastructureConfigurationError("Nacos infrastructure config must be a mapping")
    return value


def load_infrastructure_settings(*, force: bool = False) -> InfrastructureSettings:
    """Load MongoDB/Redis locations from Nacos, with environment fallbacks."""
    global _CACHED
    now = time.monotonic()
    with _LOCK:
        if not force and _CACHED is not None and now - _CACHED[0] < 60:
            return _CACHED[1]
        environment = effective_environment(BACKEND_ROOT)
        nacos = _load_nacos(environment)
        mongodb = _mapping(nacos.get("mongodb"))
        redis = _mapping(nacos.get("redis"))
        settings = InfrastructureSettings(
            mongodb_uri=str(environment.get("METRICS_MONGODB_URI") or mongodb.get("uri") or "").strip() or None,
            mongodb_database=str(
                environment.get("METRICS_MONGODB_DATABASE")
                or mongodb.get("database")
                or "agent_eval_metrics"
            ),
            redis_url=str(environment.get("AGENT_EVAL_REDIS_URL") or redis.get("url") or "").strip() or None,
            source="nacos" if nacos else "environment",
        )
        _CACHED = (now, settings)
        return settings


def infrastructure_health() -> dict[str, Any]:
    """Return non-secret configuration readiness for diagnostics and settings UI."""
    try:
        settings = load_infrastructure_settings(force=True)
    except Exception as exc:
        return {"status": "error", "error": str(exc), "mongodb": False, "redis": False}
    return {
        "status": "configured" if settings.mongodb_uri else "not_configured",
        "source": settings.source,
        "mongodb": bool(settings.mongodb_uri),
        "mongodb_database": settings.mongodb_database,
        "redis": bool(settings.redis_url),
    }
