from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id TEXT PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    password_salt TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'user',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tokens (
    token_hash TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_tokens_user ON tokens(user_id);
"""

ROLE_ADMIN = "admin"
ROLE_USER = "user"
ROLES = (ROLE_ADMIN, ROLE_USER)


class IdentityDbError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class StoredUser:
    user_id: str
    username: str
    password_salt: str
    password_hash: str
    role: str
    created_at: str
    updated_at: str


class IdentityRepository(Protocol):
    """用户注册与 token 的持久化接口。默认 SQLite，可替换为其他库。"""

    def get_user_by_id(self, user_id: str) -> StoredUser | None: ...

    def get_user_by_username(self, username: str) -> StoredUser | None: ...

    def get_user_by_token_hash(self, token_hash: str) -> StoredUser | None: ...

    def insert_user(self, user: StoredUser) -> None: ...

    def touch_user(self, user_id: str, updated_at: str) -> None: ...

    def insert_token(self, user_id: str, token_hash: str) -> None: ...

    def list_users(self) -> list[StoredUser]: ...

    def update_user(
        self,
        user_id: str,
        *,
        username: str | None = None,
        password_salt: str | None = None,
        password_hash: str | None = None,
        role: str | None = None,
        updated_at: str,
    ) -> None: ...

    def delete_user(self, user_id: str) -> None: ...

    def delete_token(self, token_hash: str) -> None: ...

    def delete_other_tokens(self, user_id: str, keep_token_hash: str) -> None: ...


class SqliteIdentityRepository:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.executescript(SCHEMA)
        self._migrate()
        self._conn.commit()

    def _migrate(self) -> None:
        columns = {
            str(row[1])
            for row in self._conn.execute("PRAGMA table_info(users)").fetchall()
        }
        if "role" not in columns:
            self._conn.execute(
                "ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'user'"
            )
            first = self._conn.execute(
                "SELECT user_id FROM users ORDER BY created_at ASC LIMIT 1"
            ).fetchone()
            if first is not None:
                self._conn.execute(
                    "UPDATE users SET role = ? WHERE user_id = ?",
                    (ROLE_ADMIN, str(first[0])),
                )

    def get_user_by_id(self, user_id: str) -> StoredUser | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM users WHERE user_id = ?", (user_id,)
            ).fetchone()
            return None if row is None else self._hydrate(row)

    def get_user_by_username(self, username: str) -> StoredUser | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM users WHERE username = ?", (username,)
            ).fetchone()
            return None if row is None else self._hydrate(row)

    def get_user_by_token_hash(self, token_hash: str) -> StoredUser | None:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT users.* FROM users
                JOIN tokens ON tokens.user_id = users.user_id
                WHERE tokens.token_hash = ?
                """,
                (token_hash,),
            ).fetchone()
            return None if row is None else self._hydrate(row)

    def insert_user(self, user: StoredUser) -> None:
        with self._lock:
            try:
                self._conn.execute(
                    """
                    INSERT INTO users (
                        user_id, username, password_salt, password_hash, role,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        user.user_id,
                        user.username,
                        user.password_salt,
                        user.password_hash,
                        user.role,
                        user.created_at,
                        user.updated_at,
                    ),
                )
                self._conn.commit()
            except sqlite3.IntegrityError as exc:
                self._conn.rollback()
                raise IdentityDbError(409, "username already exists") from exc

    def touch_user(self, user_id: str, updated_at: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE users SET updated_at = ? WHERE user_id = ?",
                (updated_at, user_id),
            )
            self._conn.commit()

    def insert_token(self, user_id: str, token_hash: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO tokens (token_hash, user_id) VALUES (?, ?)",
                (token_hash, user_id),
            )
            self._conn.commit()

    def list_users(self) -> list[StoredUser]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM users ORDER BY created_at ASC"
            ).fetchall()
            return [self._hydrate(row) for row in rows]

    def update_user(
        self,
        user_id: str,
        *,
        username: str | None = None,
        password_salt: str | None = None,
        password_hash: str | None = None,
        role: str | None = None,
        updated_at: str,
    ) -> None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM users WHERE user_id = ?", (user_id,)
            ).fetchone()
            if row is None:
                raise IdentityDbError(404, "user not found")
            next_username = username if username is not None else str(row["username"])
            next_salt = (
                password_salt if password_salt is not None else str(row["password_salt"])
            )
            next_hash = (
                password_hash if password_hash is not None else str(row["password_hash"])
            )
            next_role = role if role is not None else str(row["role"])
            try:
                self._conn.execute(
                    """
                    UPDATE users SET
                        username = ?, password_salt = ?, password_hash = ?,
                        role = ?, updated_at = ?
                    WHERE user_id = ?
                    """,
                    (next_username, next_salt, next_hash, next_role, updated_at, user_id),
                )
                self._conn.commit()
            except sqlite3.IntegrityError as extra:
                self._conn.rollback()
                raise IdentityDbError(409, "username already exists") from extra

    def delete_user(self, user_id: str) -> None:
        with self._lock:
            cursor = self._conn.execute(
                "DELETE FROM users WHERE user_id = ?", (user_id,)
            )
            self._conn.commit()
            if cursor.rowcount == 0:
                raise IdentityDbError(404, "user not found")

    def delete_token(self, token_hash: str) -> None:
        with self._lock:
            self._conn.execute(
                "DELETE FROM tokens WHERE token_hash = ?", (token_hash,)
            )
            self._conn.commit()

    def delete_other_tokens(self, user_id: str, keep_token_hash: str) -> None:
        with self._lock:
            self._conn.execute(
                "DELETE FROM tokens WHERE user_id = ? AND token_hash != ?",
                (user_id, keep_token_hash),
            )
            self._conn.commit()

    def _hydrate(self, row: sqlite3.Row) -> StoredUser:
        return StoredUser(
            user_id=str(row["user_id"]),
            username=str(row["username"]),
            password_salt=str(row["password_salt"]),
            password_hash=str(row["password_hash"]),
            role=str(row["role"] if "role" in row.keys() else ROLE_USER),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )
