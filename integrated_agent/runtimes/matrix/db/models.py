from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import ForeignKey, Index, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


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


@dataclass(frozen=True)
class StoredSession:
    session_id: str
    user_id: str
    title: str
    status: str
    last_scenario: str | None
    created_at: str
    updated_at: str
    last_active_at: str


@dataclass(frozen=True)
class StoredTurn:
    turn_id: str
    session_id: str
    role: str
    text: str
    task_id: str | None
    extra_json: str | None
    created_at: str


class Base(DeclarativeBase):
    pass


class UserRow(Base):
    __tablename__ = "users"

    user_id: Mapped[str] = mapped_column(String, primary_key=True)
    username: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    password_salt: Mapped[str] = mapped_column(String, nullable=False)
    password_hash: Mapped[str] = mapped_column(String, nullable=False)
    role: Mapped[str] = mapped_column(String, nullable=False, default=ROLE_USER)
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    updated_at: Mapped[str] = mapped_column(String, nullable=False)

    tokens: Mapped[list[TokenRow]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    sessions: Mapped[list[SessionRow]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class TokenRow(Base):
    __tablename__ = "tokens"
    __table_args__ = (Index("idx_tokens_user", "user_id"),)

    token_hash: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.user_id", ondelete="CASCADE"),
        nullable=False,
    )

    user: Mapped[UserRow] = relationship(back_populates="tokens")


class SessionRow(Base):
    __tablename__ = "sessions"
    __table_args__ = (Index("idx_sessions_user", "user_id", "last_active_at"),)

    session_id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.user_id", ondelete="CASCADE"),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="active")
    last_scenario: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    updated_at: Mapped[str] = mapped_column(String, nullable=False)
    last_active_at: Mapped[str] = mapped_column(String, nullable=False)

    user: Mapped[UserRow] = relationship(back_populates="sessions")
    turns: Mapped[list[TurnRow]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class TurnRow(Base):
    __tablename__ = "session_turns"
    __table_args__ = (Index("idx_session_turns_session", "session_id", "created_at"),)

    turn_id: Mapped[str] = mapped_column(String, primary_key=True)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("sessions.session_id", ondelete="CASCADE"),
        nullable=False,
    )
    role: Mapped[str] = mapped_column(String, nullable=False)
    text: Mapped[str] = mapped_column(String, nullable=False)
    task_id: Mapped[str | None] = mapped_column(String, nullable=True)
    extra_json: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[str] = mapped_column(String, nullable=False)

    session: Mapped[SessionRow] = relationship(back_populates="turns")
