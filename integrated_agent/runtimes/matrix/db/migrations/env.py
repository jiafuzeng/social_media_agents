"""Alembic 环境：身份库 SQLite（与 IdentityStore 的 identity.sqlite 同一份）。"""

from __future__ import annotations

import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool


def _project_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "run_server.py").exists():
            return parent
    raise RuntimeError("cannot locate project root from alembic env")


project_root = _project_root()
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from integrated_agent.runtimes.matrix.db.models import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

sqlite_path = Path(
    os.environ.get(
        "IDENTITY_SQLITE",
        project_root / "workspace" / "identity" / "identity.sqlite",
    )
).expanduser().resolve()
sqlite_path.parent.mkdir(parents=True, exist_ok=True)
config.set_main_option("sqlalchemy.url", f"sqlite:///{sqlite_path}")

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
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
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
