from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Protocol

from sqlalchemy import delete, event, inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from .models import (
    ROLE_ADMIN,
    Base,
    IdentityDbError,
    SessionRow,
    StoredSession,
    StoredTurn,
    StoredUser,
    TokenRow,
    TurnRow,
    UserRow,
)


class IdentityRepository(Protocol):
    """用户注册与 token 的异步持久化接口。默认 SQLAlchemy AsyncSession + SQLite。"""

    async def get_user_by_id(self, user_id: str) -> StoredUser | None: ...

    async def get_user_by_username(self, username: str) -> StoredUser | None: ...

    async def get_user_by_token_hash(self, token_hash: str) -> StoredUser | None: ...

    async def insert_user(self, user: StoredUser) -> None: ...

    async def touch_user(self, user_id: str, updated_at: str) -> None: ...

    async def insert_token(self, user_id: str, token_hash: str) -> None: ...

    async def list_users(self) -> list[StoredUser]: ...

    async def update_user(
        self,
        user_id: str,
        *,
        username: str | None = None,
        password_salt: str | None = None,
        password_hash: str | None = None,
        role: str | None = None,
        updated_at: str,
    ) -> None: ...

    async def delete_user(self, user_id: str) -> None: ...

    async def delete_token(self, token_hash: str) -> None: ...

    async def delete_other_tokens(self, user_id: str, keep_token_hash: str) -> None: ...

    async def insert_session(self, session: StoredSession) -> None: ...

    async def get_session(self, session_id: str) -> StoredSession | None: ...

    async def list_sessions(self, user_id: str) -> list[StoredSession]: ...

    async def update_session(
        self,
        session_id: str,
        *,
        title: str | None = None,
        status: str | None = None,
        last_scenario: str | None = None,
        updated_at: str,
        last_active_at: str | None = None,
    ) -> None: ...

    async def delete_session(self, session_id: str) -> None: ...

    async def insert_turn(self, turn: StoredTurn) -> None: ...

    async def list_turns(self, session_id: str) -> list[StoredTurn]: ...


def _stored_user(row: UserRow) -> StoredUser:
    return StoredUser(
        user_id=row.user_id,
        username=row.username,
        password_salt=row.password_salt,
        password_hash=row.password_hash,
        role=row.role,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _stored_session(row: SessionRow) -> StoredSession:
    return StoredSession(
        session_id=row.session_id,
        user_id=row.user_id,
        title=row.title,
        status=row.status,
        last_scenario=row.last_scenario,
        created_at=row.created_at,
        updated_at=row.updated_at,
        last_active_at=row.last_active_at,
    )


def _stored_turn(row: TurnRow) -> StoredTurn:
    return StoredTurn(
        turn_id=row.turn_id,
        session_id=row.session_id,
        role=row.role,
        text=row.text,
        task_id=row.task_id,
        extra_json=row.extra_json,
        created_at=row.created_at,
    )


class SqlAlchemyIdentityRepository:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._engine = create_async_engine(
            f"sqlite+aiosqlite:///{path}",
        )
        self._Session = async_sessionmaker(
            self._engine,
            expire_on_commit=False,
            class_=AsyncSession,
        )
        self._ready = False
        self._init_lock = asyncio.Lock()

        @event.listens_for(self._engine.sync_engine, "connect")
        def _set_sqlite_pragma(dbapi_connection, _connection_record) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.close()

    async def _ensure(self) -> None:
        if self._ready:
            return
        async with self._init_lock:
            if self._ready:
                return
            async with self._engine.begin() as connection:
                await self._migrate(connection)
                await connection.run_sync(Base.metadata.create_all)
            self._ready = True

    async def _migrate(self, connection) -> None:
        def existing_columns(sync_connection):
            inspector = inspect(sync_connection)
            if "users" not in inspector.get_table_names():
                return None
            return {column["name"] for column in inspector.get_columns("users")}

        columns = await connection.run_sync(existing_columns)
        if columns is None or "role" in columns:
            return
        await connection.execute(
            text("ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'user'")
        )
        first = (
            await connection.execute(
                text("SELECT user_id FROM users ORDER BY created_at ASC LIMIT 1")
            )
        ).first()
        if first is not None:
            await connection.execute(
                text("UPDATE users SET role = :role WHERE user_id = :user_id"),
                {"role": ROLE_ADMIN, "user_id": first[0]},
            )

    async def get_user_by_id(self, user_id: str) -> StoredUser | None:
        await self._ensure()
        async with self._Session() as db:
            row = await db.get(UserRow, user_id)
            return None if row is None else _stored_user(row)

    async def get_user_by_username(self, username: str) -> StoredUser | None:
        await self._ensure()
        async with self._Session() as db:
            row = await db.scalar(select(UserRow).where(UserRow.username == username))
            return None if row is None else _stored_user(row)

    async def get_user_by_token_hash(self, token_hash: str) -> StoredUser | None:
        await self._ensure()
        async with self._Session() as db:
            row = await db.scalar(
                select(UserRow)
                .join(TokenRow)
                .where(TokenRow.token_hash == token_hash)
            )
            return None if row is None else _stored_user(row)

    async def insert_user(self, user: StoredUser) -> None:
        await self._ensure()
        async with self._Session() as db:
            db.add(
                UserRow(
                    user_id=user.user_id,
                    username=user.username,
                    password_salt=user.password_salt,
                    password_hash=user.password_hash,
                    role=user.role,
                    created_at=user.created_at,
                    updated_at=user.updated_at,
                )
            )
            try:
                await db.commit()
            except IntegrityError as error:
                await db.rollback()
                raise IdentityDbError(409, "username already exists") from error

    async def touch_user(self, user_id: str, updated_at: str) -> None:
        await self._ensure()
        async with self._Session() as db:
            row = await db.get(UserRow, user_id)
            if row is None:
                return
            row.updated_at = updated_at
            await db.commit()

    async def insert_token(self, user_id: str, token_hash: str) -> None:
        await self._ensure()
        async with self._Session() as db:
            db.add(TokenRow(token_hash=token_hash, user_id=user_id))
            await db.commit()

    async def list_users(self) -> list[StoredUser]:
        await self._ensure()
        async with self._Session() as db:
            rows = (await db.scalars(select(UserRow).order_by(UserRow.created_at.asc()))).all()
            return [_stored_user(row) for row in rows]

    async def update_user(
        self,
        user_id: str,
        *,
        username: str | None = None,
        password_salt: str | None = None,
        password_hash: str | None = None,
        role: str | None = None,
        updated_at: str,
    ) -> None:
        await self._ensure()
        async with self._Session() as db:
            row = await db.get(UserRow, user_id)
            if row is None:
                raise IdentityDbError(404, "user not found")
            if username is not None:
                row.username = username
            if password_salt is not None:
                row.password_salt = password_salt
            if password_hash is not None:
                row.password_hash = password_hash
            if role is not None:
                row.role = role
            row.updated_at = updated_at
            try:
                await db.commit()
            except IntegrityError as error:
                await db.rollback()
                raise IdentityDbError(409, "username already exists") from error

    async def delete_user(self, user_id: str) -> None:
        await self._ensure()
        async with self._Session() as db:
            row = await db.get(UserRow, user_id)
            if row is None:
                raise IdentityDbError(404, "user not found")
            await db.delete(row)
            await db.commit()

    async def delete_token(self, token_hash: str) -> None:
        await self._ensure()
        async with self._Session() as db:
            row = await db.get(TokenRow, token_hash)
            if row is not None:
                await db.delete(row)
                await db.commit()

    async def delete_other_tokens(self, user_id: str, keep_token_hash: str) -> None:
        await self._ensure()
        async with self._Session() as db:
            await db.execute(
                delete(TokenRow).where(
                    TokenRow.user_id == user_id,
                    TokenRow.token_hash != keep_token_hash,
                )
            )
            await db.commit()

    async def insert_session(self, session: StoredSession) -> None:
        await self._ensure()
        async with self._Session() as db:
            db.add(
                SessionRow(
                    session_id=session.session_id,
                    user_id=session.user_id,
                    title=session.title,
                    status=session.status,
                    last_scenario=session.last_scenario,
                    created_at=session.created_at,
                    updated_at=session.updated_at,
                    last_active_at=session.last_active_at,
                )
            )
            await db.commit()

    async def get_session(self, session_id: str) -> StoredSession | None:
        await self._ensure()
        async with self._Session() as db:
            row = await db.get(SessionRow, session_id)
            return None if row is None else _stored_session(row)

    async def list_sessions(self, user_id: str) -> list[StoredSession]:
        await self._ensure()
        async with self._Session() as db:
            rows = (
                await db.scalars(
                    select(SessionRow)
                    .where(SessionRow.user_id == user_id, SessionRow.status == "active")
                    .order_by(SessionRow.last_active_at.desc())
                )
            ).all()
            return [_stored_session(row) for row in rows]

    async def update_session(
        self,
        session_id: str,
        *,
        title: str | None = None,
        status: str | None = None,
        last_scenario: str | None = None,
        updated_at: str,
        last_active_at: str | None = None,
    ) -> None:
        await self._ensure()
        async with self._Session() as db:
            row = await db.get(SessionRow, session_id)
            if row is None:
                raise IdentityDbError(404, "session not found")
            if title is not None:
                row.title = title
            if status is not None:
                row.status = status
            if last_scenario is not None:
                row.last_scenario = last_scenario
            row.updated_at = updated_at
            if last_active_at is not None:
                row.last_active_at = last_active_at
            await db.commit()

    async def delete_session(self, session_id: str) -> None:
        await self._ensure()
        async with self._Session() as db:
            row = await db.get(SessionRow, session_id)
            if row is None:
                raise IdentityDbError(404, "session not found")
            await db.delete(row)
            await db.commit()

    async def insert_turn(self, turn: StoredTurn) -> None:
        await self._ensure()
        async with self._Session() as db:
            db.add(
                TurnRow(
                    turn_id=turn.turn_id,
                    session_id=turn.session_id,
                    role=turn.role,
                    text=turn.text,
                    task_id=turn.task_id,
                    extra_json=turn.extra_json,
                    created_at=turn.created_at,
                )
            )
            await db.commit()

    async def list_turns(self, session_id: str) -> list[StoredTurn]:
        await self._ensure()
        async with self._Session() as db:
            rows = (
                await db.scalars(
                    select(TurnRow)
                    .where(TurnRow.session_id == session_id)
                    .order_by(TurnRow.created_at.asc())
                )
            ).all()
            return [_stored_turn(row) for row in rows]


SqliteIdentityRepository = SqlAlchemyIdentityRepository
