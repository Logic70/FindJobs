"""Alembic environment configuration for FindJobs.

This file is loaded by Alembic at runtime.  The engine is supplied
programmatically from findjobs.db so that it works with any SQLite path
(in-memory, temp file, or persistent).  No migration is ever run from the
raw CLI without first configuring the engine.
"""

import logging

from alembic import context
from sqlalchemy import engine_from_config, pool

# Register models so that alembic can inspect target_metadata.
from findjobs.models import Base

config = context.config
target_metadata = Base.metadata
logger = logging.getLogger("alembic.env")


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (emit DDL as SQL text)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live connection.

    The caller should set ``config.attributes["engine"]`` before calling
    ``command.upgrade``.  If no engine has been set (e.g. raw CLI usage
    without our wrapper) fall back to *sqlalchemy.url* from the ini file.
    """
    connectable = config.attributes.get("engine")
    if connectable is None:
        connectable = engine_from_config(
            config.get_section(config.config_ini_section, {}),
            prefix="sqlalchemy.",
            poolclass=pool.NullPool,
        )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
