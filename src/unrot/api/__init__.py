"""unrot's backend: a JSON API over the event log, and the frontend it serves."""

from .app import app, create_app

__all__ = ["app", "create_app"]
