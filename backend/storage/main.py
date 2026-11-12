"""ASGI entry point for a storage-node container."""

from backend.storage.application import create_app

app = create_app()
