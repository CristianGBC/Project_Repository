"""HTTP routes exposed by the coordinator."""

from backend.app.api.authentication import router as authentication_router
from backend.app.api.catalogs import router as catalogs_router
from backend.app.api.documents import router as documents_router
from backend.app.api.operations import router as operations_router
from backend.app.api.routes import router
from backend.app.api.subjects import router as subjects_router
from backend.app.api.users import router as users_router

__all__ = [
    "authentication_router",
    "catalogs_router",
    "documents_router",
    "operations_router",
    "router",
    "subjects_router",
    "users_router",
]
