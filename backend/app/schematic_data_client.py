from __future__ import annotations

from typing import Any, Iterable, Mapping

import httpx

from app.infrastructure_config import InfrastructureConfigurationError, load_infrastructure_settings
from app.response_cache import cache_key, clear_response_cache, get_cached_json, set_cached_json


RATIONALITY_COLLECTION = "HDschematicRationalityCollection"


def _records(payload: Any) -> tuple[list[dict[str, Any]], int | None]:
    """Normalize common list and paginated response envelopes."""
    if isinstance(payload, list):
        rows = payload
        total = len(rows)
    elif isinstance(payload, Mapping):
        current: Any = payload
        for name in ("data", "result"):
            nested = current.get(name) if isinstance(current, Mapping) else None
            if isinstance(nested, (Mapping, list)):
                current = nested
                break
        if isinstance(current, list):
            rows, total = current, len(current)
        else:
            rows = []
            for name in ("records", "items", "list", "rows", "content"):
                candidate = current.get(name) if isinstance(current, Mapping) else None
                if isinstance(candidate, list):
                    rows = candidate
                    break
            if not rows and isinstance(current, Mapping) and "sessionId" in current:
                rows = [current]
            total_value = next(
                (
                    current.get(name)
                    for name in ("total", "totalCount", "count")
                    if isinstance(current, Mapping) and current.get(name) is not None
                ),
                None,
            )
            try:
                total = int(total_value) if total_value is not None else None
            except (TypeError, ValueError):
                total = None
    else:
        raise InfrastructureConfigurationError("原理图数据查询接口返回的不是 JSON 对象或数组")
    return [dict(row) for row in rows if isinstance(row, Mapping)], total


class SchematicDataClient:
    def __init__(self) -> None:
        self.settings = load_infrastructure_settings()
        if not self.settings.schematic_data_api_base_url:
            raise InfrastructureConfigurationError("SCHEMATIC_DATA_API_BASE_URL 尚未配置")

    @property
    def query_url(self) -> str:
        return self.settings.schematic_data_api_base_url + self.settings.schematic_data_query_path

    @property
    def write_url(self) -> str:
        if not self.settings.schematic_data_write_path:
            raise InfrastructureConfigurationError("SCHEMATIC_DATA_WRITE_PATH 尚未配置")
        return self.settings.schematic_data_api_base_url + self.settings.schematic_data_write_path

    def _headers(self) -> dict[str, str]:
        return (
            {"Cookie": self.settings.schematic_data_api_cookie}
            if self.settings.schematic_data_api_cookie
            else {}
        )

    def query_page(
        self,
        *,
        collection_name: str,
        page: int = 1,
        size: int | None = None,
        session_id: str | None = None,
        use_cache: bool = True,
    ) -> tuple[list[dict[str, Any]], int | None]:
        payload = self.query_payload(
            collection_name=collection_name,
            page=page,
            size=size,
            session_id=session_id,
            use_cache=use_cache,
        )
        return _records(payload)

    def query_payload(
        self,
        *,
        collection_name: str,
        page: int = 1,
        size: int | None = None,
        session_id: str | None = None,
        use_cache: bool = True,
    ) -> Any:
        """Return the Java query API JSON without changing its response envelope."""
        actual_size = size or self.settings.schematic_data_query_page_size
        params = {"collectionName": collection_name, "page": page, "size": actual_size}
        if session_id:
            params["sessionId"] = session_id
        key = cache_key("schematic-data-query", {"url": self.query_url, **params})
        payload = get_cached_json(key) if use_cache else None
        if payload is None:
            try:
                response = httpx.get(
                    self.query_url,
                    params=params,
                    headers=self._headers(),
                    timeout=self.settings.schematic_data_timeout_seconds,
                    trust_env=False,
                )
                response.raise_for_status()
                payload = response.json()
            except (httpx.HTTPError, ValueError) as exc:
                raise InfrastructureConfigurationError(f"原理图数据查询接口调用失败: {exc}") from exc
            set_cached_json(key, payload, ttl_seconds=self.settings.cache_default_ttl_seconds)
        return payload

    def insert_record(
        self,
        record: Mapping[str, Any],
        *,
        collection_name: str = RATIONALITY_COLLECTION,
    ) -> Any:
        """Insert one analysis record through the configured Java facade."""
        try:
            response = httpx.post(
                self.write_url,
                params={"collectionName": collection_name},
                json=dict(record),
                headers=self._headers(),
                timeout=self.settings.schematic_data_timeout_seconds,
                trust_env=False,
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise InfrastructureConfigurationError(f"原理图数据写入接口调用失败: {exc}") from exc
        clear_response_cache()
        return payload

    def find_rationality_records(self, identifiers: Iterable[str]) -> list[dict[str, Any]]:
        expected = {str(item) for item in identifiers if str(item)}
        if not expected:
            return []
        matches: list[dict[str, Any]] = []
        page_size = self.settings.schematic_data_query_page_size
        for identifier in sorted(expected):
            for page in range(1, self.settings.schematic_data_query_max_pages + 1):
                rows, total = self.query_page(
                    collection_name=RATIONALITY_COLLECTION,
                    page=page,
                    size=page_size,
                    session_id=identifier,
                )
                matches.extend(row for row in rows if str(row.get("sessionId") or "") == identifier)
                if not rows or len(rows) < page_size or (total is not None and page * page_size >= total):
                    break
        return matches


def schematic_data_health() -> dict[str, Any]:
    try:
        client = SchematicDataClient()
        client.query_page(collection_name=RATIONALITY_COLLECTION, page=1, use_cache=False)
        return {"status": "ok", "endpoint": client.settings.schematic_data_query_path}
    except Exception as exc:
        return {"status": "unavailable", "detail": str(exc)}
