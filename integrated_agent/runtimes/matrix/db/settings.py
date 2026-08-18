from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.engine.url import make_url

from integrated_agent.config import PROJECT_ROOT

IDENTITY_BACKENDS = ("sqlite", "mysql")


@dataclass(frozen=True)
class IdentityDbSettings:
    backend: str
    async_url: str
    sync_url: str
    sqlite_path: Path | None


def _with_mysql_charset(url: str) -> str:
    parsed = make_url(url)
    if not parsed.drivername.startswith("mysql"):
        return url
    if "charset" in parsed.query:
        return url
    return parsed.update_query_pairs((("charset", "utf8mb4"),)).render_as_string(
        hide_password=False
    )


def to_async_url(raw: str) -> str:
    parsed = make_url(raw.strip())
    if parsed.drivername in {"sqlite", "sqlite+aiosqlite"}:
        return parsed.set(drivername="sqlite+aiosqlite").render_as_string(
            hide_password=False
        )
    if parsed.drivername.startswith("mysql"):
        return _with_mysql_charset(
            parsed.set(drivername="mysql+asyncmy").render_as_string(hide_password=False)
        )
    raise ValueError(
        f"unsupported identity database url: {parsed.drivername}. "
        "use sqlite or mysql"
    )


def to_sync_url(raw: str) -> str:
    parsed = make_url(raw.strip())
    if parsed.drivername in {"sqlite", "sqlite+aiosqlite"}:
        return parsed.set(drivername="sqlite").render_as_string(hide_password=False)
    if parsed.drivername.startswith("mysql"):
        return _with_mysql_charset(
            parsed.set(drivername="mysql+pymysql").render_as_string(hide_password=False)
        )
    raise ValueError(
        f"unsupported identity database url: {parsed.drivername}. "
        "use sqlite or mysql"
    )


def sqlite_urls(path: Path) -> tuple[str, str]:
    resolved = path.expanduser().resolve()
    return f"sqlite+aiosqlite:///{resolved}", f"sqlite:///{resolved}"


def sqlite_settings(path: Path) -> IdentityDbSettings:
    async_url, sync_url = sqlite_urls(path)
    return IdentityDbSettings("sqlite", async_url, sync_url, path.resolve())


def load_identity_db_settings(project_root: Path | None = None) -> IdentityDbSettings:
    root = project_root or PROJECT_ROOT
    backend = os.environ.get("IDENTITY_DB", "sqlite").strip().lower() or "sqlite"
    if backend not in IDENTITY_BACKENDS:
        raise RuntimeError(
            f"IDENTITY_DB 只能是 sqlite 或 mysql，当前为 {backend!r}"
        )
    if backend == "sqlite":
        raw_path = os.environ.get("IDENTITY_SQLITE")
        path = Path(
            raw_path
            if raw_path
            else root / "workspace" / "identity" / "identity.sqlite"
        )
        if not path.is_absolute():
            path = root / path
        async_url, sync_url = sqlite_urls(path)
        return IdentityDbSettings("sqlite", async_url, sync_url, path.resolve())
    mysql_url = os.environ.get("IDENTITY_MYSQL_URL", "").strip()
    if not mysql_url:
        raise RuntimeError("IDENTITY_DB=mysql 时必须设置 IDENTITY_MYSQL_URL")
    return IdentityDbSettings(
        "mysql",
        to_async_url(mysql_url),
        to_sync_url(mysql_url),
        None,
    )
