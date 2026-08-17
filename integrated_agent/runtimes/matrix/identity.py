from __future__ import annotations

import asyncio
import hashlib
import re
import secrets
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .identity_db import (
    ROLE_ADMIN,
    ROLE_USER,
    ROLES,
    IdentityDbError,
    IdentityRepository,
    SqliteIdentityRepository,
    StoredUser,
)
from .models import DomainModel


USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_]{2,32}$")
PBKDF2_ROUNDS = 120_000


class IdentityError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


class UserOut(DomainModel):
    user_id: str
    username: str
    role: str = ROLE_USER
    created_at: str | None = None
    updated_at: str | None = None


class UserListOut(DomainModel):
    users: list[UserOut]


class AuthOut(DomainModel):
    user: UserOut
    token: str


class RegisterIn(DomainModel):
    username: str
    password: str


class LoginIn(DomainModel):
    username: str
    password: str


class UpdateUserIn(DomainModel):
    username: str | None = None
    current_password: str | None = None
    new_password: str | None = None
    role: str | None = None


class CreateUserIn(DomainModel):
    username: str
    password: str
    role: str = ROLE_USER


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hash_password(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        bytes.fromhex(salt),
        PBKDF2_ROUNDS,
    ).hex()


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def parse_bearer_token(
    authorization: str | None = None,
    x_user_token: str | None = None,
) -> str | None:
    if x_user_token and x_user_token.strip():
        return x_user_token.strip()
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
        return token or None
    return None


def _to_user_out(user: StoredUser) -> UserOut:
    return UserOut(
        user_id=user.user_id,
        username=user.username,
        role=user.role,
        created_at=user.created_at,
        updated_at=user.updated_at,
    )


def _validate_username(username: str) -> str:
    username = username.strip()
    if not USERNAME_PATTERN.fullmatch(username):
        raise IdentityError(
            422, "username must be 2-32 letters, digits or underscore"
        )
    return username


def _validate_password(password: str) -> str:
    if len(password) < 6:
        raise IdentityError(422, "password must be at least 6 characters")
    return password


def _validate_role(role: str) -> str:
    value = role.strip()
    if value not in ROLES:
        raise IdentityError(422, "role must be admin or user")
    return value


def _is_admin(user: StoredUser) -> bool:
    return user.role == ROLE_ADMIN


class IdentityStore:
    """host 用户注册/登录。持久化走 IdentityRepository，默认 SQLite。"""

    def __init__(
        self,
        root: Path,
        *,
        repository: IdentityRepository | None = None,
    ) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()
        self.repository = repository or SqliteIdentityRepository(
            self.root / "identity.sqlite"
        )

    async def register(self, username: str, password: str) -> AuthOut:
        username = _validate_username(username)
        password = _validate_password(password)
        async with self._lock:
            role = ROLE_ADMIN if not self.repository.list_users() else ROLE_USER
            user = self._insert_user(username, password, role=role)
            token = secrets.token_urlsafe(32)
            self.repository.insert_token(user.user_id, _hash_token(token))
            return AuthOut(user=_to_user_out(user), token=token)

    def _insert_user(
        self, username: str, password: str, *, role: str = ROLE_USER
    ) -> StoredUser:
        if self.repository.get_user_by_username(username) is not None:
            raise IdentityError(409, "username already exists")
        now = _now()
        salt = secrets.token_hex(16)
        user = StoredUser(
            user_id=uuid4().hex,
            username=username,
            password_salt=salt,
            password_hash=_hash_password(password, salt),
            role=_validate_role(role),
            created_at=now,
            updated_at=now,
        )
        try:
            self.repository.insert_user(user)
        except IdentityDbError as exc:
            raise IdentityError(exc.status, str(exc)) from exc
        return user

    async def login(self, username: str, password: str) -> AuthOut:
        async with self._lock:
            user = self.repository.get_user_by_username(username.strip())
            if user is None:
                raise IdentityError(401, "invalid username or password")
            if _hash_password(password, user.password_salt) != user.password_hash:
                raise IdentityError(401, "invalid username or password")
            token = secrets.token_urlsafe(32)
            self.repository.insert_token(user.user_id, _hash_token(token))
            self.repository.touch_user(user.user_id, _now())
            return AuthOut(user=_to_user_out(user), token=token)

    async def user_for_token(self, token: str | None) -> UserOut:
        return _to_user_out(await self.stored_user_for_token(token))

    async def stored_user_for_token(self, token: str | None) -> StoredUser:
        if not token:
            raise IdentityError(401, "missing token")
        async with self._lock:
            user = self.repository.get_user_by_token_hash(_hash_token(token))
            if user is None:
                raise IdentityError(401, "invalid token")
            return user

    def _admin_count(self) -> int:
        return sum(1 for item in self.repository.list_users() if _is_admin(item))

    async def list_users(self, token: str | None) -> UserListOut:
        actor = await self.stored_user_for_token(token)
        async with self._lock:
            users = self.repository.list_users()
            if not _is_admin(actor):
                users = [item for item in users if item.user_id == actor.user_id]
            return UserListOut(users=[_to_user_out(item) for item in users])

    async def create_user(
        self,
        token: str | None,
        username: str,
        password: str,
        role: str = ROLE_USER,
    ) -> UserOut:
        actor = await self.stored_user_for_token(token)
        if not _is_admin(actor):
            raise IdentityError(403, "admin role required")
        username = _validate_username(username)
        password = _validate_password(password)
        role = _validate_role(role)
        async with self._lock:
            return _to_user_out(self._insert_user(username, password, role=role))

    async def update_user(
        self,
        token: str | None,
        user_id: str,
        *,
        username: str | None = None,
        current_password: str | None = None,
        new_password: str | None = None,
        role: str | None = None,
    ) -> UserOut:
        actor = await self.stored_user_for_token(token)
        next_username = _validate_username(username) if username is not None else None
        next_password = (
            _validate_password(new_password) if new_password is not None else None
        )
        next_role = _validate_role(role) if role is not None else None
        if next_username is None and next_password is None and next_role is None:
            raise IdentityError(422, "no user fields to update")
        async with self._lock:
            target = self.repository.get_user_by_id(user_id)
            if target is None:
                raise IdentityError(404, "user not found")
            editing_self = actor.user_id == target.user_id
            if not editing_self and not _is_admin(actor):
                raise IdentityError(403, "admin role required")
            if next_role is not None and not _is_admin(actor):
                raise IdentityError(403, "admin role required")
            if (
                next_role == ROLE_USER
                and _is_admin(target)
                and self._admin_count() <= 1
            ):
                raise IdentityError(422, "cannot demote the last admin")
            if editing_self:
                if not current_password:
                    raise IdentityError(422, "current password is required")
                if _hash_password(current_password, actor.password_salt) != actor.password_hash:
                    raise IdentityError(401, "invalid username or password")
            salt = None
            digest = None
            if next_password is not None:
                salt = secrets.token_hex(16)
                digest = _hash_password(next_password, salt)
            try:
                self.repository.update_user(
                    target.user_id,
                    username=next_username,
                    password_salt=salt,
                    password_hash=digest,
                    role=next_role,
                    updated_at=_now(),
                )
            except IdentityDbError as exc:
                raise IdentityError(exc.status, str(exc)) from exc
            if next_password is not None:
                self.repository.delete_other_tokens(
                    target.user_id, _hash_token(token or "")
                )
            updated = self.repository.get_user_by_id(target.user_id)
            if updated is None:
                raise IdentityError(404, "user not found")
            return _to_user_out(updated)

    async def delete_user(self, token: str | None, user_id: str) -> bool:
        actor = await self.stored_user_for_token(token)
        async with self._lock:
            target = self.repository.get_user_by_id(user_id)
            if target is None:
                raise IdentityError(404, "user not found")
            editing_self = actor.user_id == target.user_id
            if not _is_admin(actor):
                raise IdentityError(403, "admin role required")
            if _is_admin(target) and self._admin_count() <= 1:
                raise IdentityError(422, "cannot delete the last admin")
            if len(self.repository.list_users()) <= 1:
                raise IdentityError(422, "cannot delete the last user")
            try:
                self.repository.delete_user(user_id)
            except IdentityDbError as extra:
                raise IdentityError(extra.status, str(extra)) from extra
            return editing_self

    async def logout(self, token: str | None) -> None:
        if not token:
            raise IdentityError(401, "missing token")
        async with self._lock:
            user = self.repository.get_user_by_token_hash(_hash_token(token))
            if user is None:
                raise IdentityError(401, "invalid token")
            self.repository.delete_token(_hash_token(token))
