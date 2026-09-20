from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.infrastructure_config import InfrastructureConfigurationError
from app.schematic_data_client import RATIONALITY_COLLECTION, SchematicDataClient


router = APIRouter(prefix="/api/schematic-data", tags=["schematic-data"])


@router.get("/query")
def query_schematic_data(
    collectionName: str = Query(RATIONALITY_COLLECTION, min_length=1, max_length=128),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    refresh: bool = False,
) -> Any:
    """Call the configured read-only Java MongoDB facade without UI session auth."""
    try:
        return SchematicDataClient().query_payload(
            collection_name=collectionName,
            page=page,
            size=size,
            use_cache=not refresh,
        )
    except InfrastructureConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
