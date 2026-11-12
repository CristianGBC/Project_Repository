"""Response models exposed by the coordinator."""

from typing import Literal

from pydantic import BaseModel


class CoordinatorHealth(BaseModel):
    status: Literal["ok"] = "ok"
    service: Literal["coordinator"] = "coordinator"


class NodeStatus(BaseModel):
    node_id: str
    school: str
    status: Literal["healthy", "unavailable"]


class NodesResponse(BaseModel):
    nodes: list[NodeStatus]
