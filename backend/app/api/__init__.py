"""HTTP routes exposed by the coordinator."""

from backend.app.api.documents import router as documents_router
from backend.app.api.routes import router

__all__ = ["documents_router", "router"]
