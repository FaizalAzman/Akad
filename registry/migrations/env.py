"""Alembic environment for the Akad registry.

Runs against the connection passed by registry.database.run_migrations, or
against DATABASE_URL when invoked through the alembic CLI (see alembic.ini).
"""
from __future__ import annotations

from alembic import context

from registry import models  # noqa: F401 — registers the tables on Base.metadata
from registry.database import Base, engine

connection = context.config.attributes.get("connection")

if connection is not None:
    context.configure(connection=connection, target_metadata=Base.metadata, render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    with engine.begin() as conn:
        context.configure(connection=conn, target_metadata=Base.metadata, render_as_batch=True)
        with context.begin_transaction():
            context.run_migrations()
