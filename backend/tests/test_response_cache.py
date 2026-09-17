from app import response_cache


class Settings:
    cache_default_ttl_seconds = 60
    cache_max_size = 2


def test_memory_cache_is_lru(monkeypatch):
    monkeypatch.setattr(response_cache, "load_infrastructure_settings", lambda: Settings())
    response_cache.clear_response_cache()
    response_cache.set_cached_json("a", {"value": 1}, ttl_seconds=60)
    response_cache.set_cached_json("b", {"value": 2}, ttl_seconds=60)
    assert response_cache.get_cached_json("a") == {"value": 1}
    response_cache.set_cached_json("c", {"value": 3}, ttl_seconds=60)
    assert response_cache.get_cached_json("b") is None
    assert response_cache.get_cached_json("a") == {"value": 1}
    assert response_cache.get_cached_json("c") == {"value": 3}


def test_memory_cache_expires(monkeypatch):
    monkeypatch.setattr(response_cache, "load_infrastructure_settings", lambda: Settings())
    response_cache.clear_response_cache()
    ticks = iter([10.0, 12.0])
    monkeypatch.setattr(response_cache.time, "monotonic", lambda: next(ticks))
    response_cache.set_cached_json("expired", 1, ttl_seconds=1)
    assert response_cache.get_cached_json("expired") is None
