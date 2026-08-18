"""Alembic 环境：身份库（SQLite 或 MySQL，与 IdentityStore 同一份配置）。"""

from __future__ import annotations

import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import create_engine, pool


def _project_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "run_server.py").exists():
            return parent
    raise RuntimeError("cannot locate project root from alembic env")


project_root = _project_root()
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from integrated_agent.runtimes.matrix.db.models import Base
from integrated_agent.runtimes.matrix.db.settings import load_identity_db_settings

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

settings = load_identity_db_settings(project_root)
if settings.sqlite_path is not None:
    settings.sqlite_path.parent.mkdir(parents=True, exist_ok=True)

target_metadata = Base.metadata
sync_url = settings.sync_url
batch = settings.backend == "sqlite"


def run_migrations_offline() -> None:
    context.configure(
        url=sync_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=batch,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = create_engine(sync_url, poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=batch,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
