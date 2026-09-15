from __future__ import annotations

import hashlib
import json
import threading
from typing import Any, Mapping

from app.infrastructure_config import load_infrastructure_settings


_LOCK = threading.Lock()
_CLIENT: Any = None
_CLIENT_URL: str | None = None


def cache_key(namespace: str, values: Mapping[str, Any]) -> str:
    payload = json.dumps(values, ensure_ascii=False, sort_keys=True, default=str)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return f"agent-eval:{namespace}:{digest}"


def _client() -> Any | None:
    global _CLIENT, _CLIENT_URL
    settings = load_infrastructure_settings()
    if not settings.redis_url:
        return None
    with _LOCK:
        if _CLIENT is not None and _CLIENT_URL == settings.redis_url:
            return _CLIENT
        try:
            import redis
        except ImportError:
            return None
        candidate = redis.Redis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_connect_timeout=1.5,
            socket_timeout=2.0,
            health_check_interval=30,
        )
        try:
            candidate.ping()
        except Exception:
            return None
        _CLIENT = candidate
        _CLIENT_URL = settings.redis_url
        return _CLIENT


def get_cached_json(key: str) -> Any | None:
    client = _client()
    if client is None:
        return None
    try:
        value = client.get(key)
        return json.loads(value) if value is not None else None
    except Exception:
        return None


def set_cached_json(key: str, value: Any, *, ttl_seconds: int) -> None:
    client = _client()
    if client is None:
        return
    try:
        client.setex(
            key,
            max(1, int(ttl_seconds)),
            json.dumps(value, ensure_ascii=False, default=str),
        )
    except Exception:
        return


def response_cache_health() -> dict[str, Any]:
    settings = load_infrastructure_settings()
    if not settings.redis_url:
        return {"status": "not_configured"}
    return {"status": "ok" if _client() is not None else "unavailable"}
