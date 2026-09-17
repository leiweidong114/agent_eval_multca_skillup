from __future__ import annotations

import hashlib
import json
import threading
import time
from copy import deepcopy
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Mapping

from app.infrastructure_config import load_infrastructure_settings


@dataclass
class _Entry:
    expires_at: float
    value: Any


_LOCK = threading.RLock()
_CACHE: OrderedDict[str, _Entry] = OrderedDict()
_HITS = 0
_MISSES = 0
_EVICTIONS = 0


def cache_key(namespace: str, values: Mapping[str, Any]) -> str:
    payload = json.dumps(values, ensure_ascii=False, sort_keys=True, default=str)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return f"agent-eval:{namespace}:{digest}"


def _remove_expired(now: float) -> None:
    expired = [key for key, entry in _CACHE.items() if entry.expires_at <= now]
    for key in expired:
        _CACHE.pop(key, None)


def get_cached_json(key: str) -> Any | None:
    global _HITS, _MISSES
    now = time.monotonic()
    with _LOCK:
        entry = _CACHE.get(key)
        if entry is None or entry.expires_at <= now:
            if entry is not None:
                _CACHE.pop(key, None)
            _MISSES += 1
            return None
        _CACHE.move_to_end(key)
        _HITS += 1
        return deepcopy(entry.value)


def set_cached_json(key: str, value: Any, *, ttl_seconds: int | None = None) -> None:
    global _EVICTIONS
    settings = load_infrastructure_settings()
    ttl = max(1, int(ttl_seconds or settings.cache_default_ttl_seconds))
    now = time.monotonic()
    with _LOCK:
        _remove_expired(now)
        _CACHE[key] = _Entry(expires_at=now + ttl, value=deepcopy(value))
        _CACHE.move_to_end(key)
        while len(_CACHE) > settings.cache_max_size:
            _CACHE.popitem(last=False)
            _EVICTIONS += 1


def clear_response_cache() -> None:
    with _LOCK:
        _CACHE.clear()


def response_cache_health() -> dict[str, Any]:
    settings = load_infrastructure_settings()
    now = time.monotonic()
    with _LOCK:
        _remove_expired(now)
        return {
            "status": "ok",
            "backend": "memory_ttl_lru",
            "entries": len(_CACHE),
            "max_size": settings.cache_max_size,
            "default_ttl_seconds": settings.cache_default_ttl_seconds,
            "hits": _HITS,
            "misses": _MISSES,
            "evictions": _EVICTIONS,
        }
