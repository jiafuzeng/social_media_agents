from __future__ import annotations

from pathlib import Path

import pytest

from integrated_agent.runtimes.matrix.host.db.settings import (
    load_identity_db_settings,
    sqlite_settings,
    to_async_url,
    to_sync_url,
)


def test_default_backend_is_sqlite(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("IDENTITY_DB", "sqlite")
    monkeypatch.delenv("IDENTITY_SQLITE", raising=False)
    monkeypatch.delenv("IDENTITY_MYSQL_URL", raising=False)
    settings = load_identity_db_settings(tmp_path)
    assert settings.backend == "sqlite"
    assert settings.sqlite_path == tmp_path / "workspace" / "identity" / "identity.sqlite"
    assert settings.async_url.startswith("sqlite+aiosqlite:///")
    assert settings.sync_url.startswith("sqlite:///")


def test_mysql_backend_requires_url(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("IDENTITY_DB", "mysql")
    monkeypatch.setenv("IDENTITY_MYSQL_URL", "")
    with pytest.raises(RuntimeError, match="IDENTITY_MYSQL_URL"):
        load_identity_db_settings(tmp_path)


def test_mysql_urls_use_asyncmy_and_pymysql(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("IDENTITY_DB", "mysql")
    monkeypatch.setenv(
        "IDENTITY_MYSQL_URL",
        "mysql://user:pass@127.0.0.1:3306/matrix_identity",
    )
    settings = load_identity_db_settings(tmp_path)
    assert settings.backend == "mysql"
    assert settings.sqlite_path is None
    assert settings.async_url.startswith("mysql+asyncmy://")
    assert "charset=utf8mb4" in settings.async_url
    assert settings.sync_url.startswith("mysql+pymysql://")


def test_reject_unknown_backend(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("IDENTITY_DB", "postgres")
    with pytest.raises(RuntimeError, match="sqlite 或 mysql"):
        load_identity_db_settings(tmp_path)


def test_sqlite_settings_helper(tmp_path: Path) -> None:
    path = tmp_path / "identity.sqlite"
    settings = sqlite_settings(path)
    assert settings.backend == "sqlite"
    assert settings.sqlite_path == path.resolve()
    assert settings.async_url.startswith("sqlite+aiosqlite:///")
    assert settings.sync_url.startswith("sqlite:///")


def test_url_helpers_keep_sqlite_and_rewrite_mysql() -> None:
    assert to_async_url("sqlite:///tmp/a.db").startswith("sqlite+aiosqlite:///")
    assert to_sync_url("sqlite+aiosqlite:///tmp/a.db").startswith("sqlite:///")
    assert to_async_url("mysql+pymysql://u:p@h/db").startswith("mysql+asyncmy://")
    assert to_sync_url("mysql+asyncmy://u:p@h/db").startswith("mysql+pymysql://")
