from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.infrastructure_config import InfrastructureConfigurationError
from app.schematic_data_client import RATIONALITY_COLLECTION, SchematicDataClient


router = APIRouter(prefix="/api/schematic-data", tags=["schematic-data"])


class SchematicDataInsertRequest(BaseModel):
    uuid: str = Field(min_length=1, max_length=128)
    status: str = Field(min_length=1, max_length=64)
    createUser: str = Field(min_length=1, max_length=128)
    createTime: str = Field(min_length=1, max_length=64)
    checkType: str = Field(min_length=1, max_length=128)
    checkMessage: str = Field(min_length=1, max_length=1000)
    userName: str = Field(min_length=1, max_length=256)
    hscopeProjectId: str = Field(min_length=1, max_length=256)
    boardNum: str = Field(min_length=1, max_length=128)
    sessionId: str = Field(min_length=1, max_length=256)
    resultText: str = Field(min_length=1, max_length=2_000_000)


@router.get("/query")
def query_schematic_data(
    collectionName: str = Query(RATIONALITY_COLLECTION, min_length=1, max_length=128),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    sessionId: str | None = Query(None, min_length=1, max_length=256),
    refresh: bool = False,
) -> Any:
    """Call the configured read-only Java MongoDB facade without UI session auth."""
    try:
        return SchematicDataClient().query_payload(
            collection_name=collectionName,
            page=page,
            size=size,
            session_id=sessionId,
            use_cache=not refresh,
        )
    except InfrastructureConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/insert", status_code=201)
def insert_schematic_data(
    payload: SchematicDataInsertRequest,
    collectionName: str = Query(RATIONALITY_COLLECTION),
) -> Any:
    """Write one validated record without requiring a UI login session."""
    try:
        return SchematicDataClient().insert_record(
            payload.model_dump(),
            collection_name=collectionName,
        )
    except InfrastructureConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
