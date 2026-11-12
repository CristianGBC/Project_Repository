"""FastAPI application shared by all six logical storage nodes."""

import os
from typing import Literal

from fastapi import FastAPI
from pydantic import BaseModel


class StorageHealth(BaseModel):
    status: Literal["ok"] = "ok"
    service: Literal["storage"] = "storage"
    node_id: str
    school: str


def create_app(
    node_id: str | None = None,
    school_name: str | None = None,
) -> FastAPI:
    """Build one node using explicit values or its container environment."""

    configured_node_id = node_id or os.getenv("NODE_ID")
    configured_school = school_name or os.getenv("SCHOOL_NAME")
    if not configured_node_id or not configured_school:
        raise RuntimeError("NODE_ID and SCHOOL_NAME must be configured")

    application = FastAPI(
        title=f"YachayRepo Storage - {configured_node_id}",
        version="0.1.0",
    )

    @application.get("/health", response_model=StorageHealth)
    async def storage_health() -> StorageHealth:
        return StorageHealth(
            node_id=configured_node_id,
            school=configured_school,
        )

    return application
