"""Declarative base shared by all YachayRepo catalog models."""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Base class whose metadata is consumed by Alembic."""
