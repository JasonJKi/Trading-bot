"""Alembic environment — wired into the project's Settings + Base metadata.

Online mode connects to `Settings.database_url` and runs migrations against it.
Offline mode renders SQL to stdout (useful for review).

SQLite needs `render_as_batch=True` for ALTER TABLE; we detect the dialect at
runtime so the same migration files work against Postgres without changes.
"""
from __future__ import annotations

import logging
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.config import get_settings  # noqa: E402
from src.core.store import Base  # noqa: E402

config = context.config

# Only apply alembic.ini's logging config when the host process hasn't
# configured logging yet (i.e. invoked via the `alembic` CLI). When called
# from init_db() at app startup, the host's setup_logging() has already
# configured handlers — don't clobber them with INFO chatter from alembic.
if config.config_file_name is not None and not logging.getLogger().handlers:
    fileConfig(config.config_file_name)

# Override the sqlalchemy.url from project settings — single source of truth.
config.set_main_option("sqlalchemy.url", get_settings().database_url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=bool(url and url.startswith("sqlite")),
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        is_sqlite = connection.dialect.name == "sqlite"
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=is_sqlite,
            compare_type=True,
            compare_server_default=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
