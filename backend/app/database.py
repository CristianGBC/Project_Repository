"""PostgreSQL engine helpers, kept independent from the Stage 3 endpoints."""

import os

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker


def get_database_url() -> str:
    """Return the configured PostgreSQL URL or fail without an unsafe fallback."""

    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL must be configured for database operations")
    if not database_url.startswith("postgresql+psycopg://"):
        raise ValueError("DATABASE_URL must use the postgresql+psycopg driver")
    return database_url


def create_database_engine(database_url: str | None = None) -> Engine:
    """Create a SQLAlchemy engine without connecting eagerly."""

    return create_engine(database_url or get_database_url(), pool_pre_ping=True)


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Build the session factory used by database maintenance commands."""

    return sessionmaker(bind=engine, expire_on_commit=False)
