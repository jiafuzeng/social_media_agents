from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException

from integrated_agent.runtimes.matrix.identity import (
    AuthOut,
    CreateUserIn,
    IdentityError,
    IdentityStore,
    LoginIn,
    RegisterIn,
    UpdateUserIn,
    UserListOut,
    UserOut,
    parse_bearer_token,
)


def _raise_identity(exc: IdentityError) -> None:
    """把 IdentityStore 的业务错误转成 HTTP 状态码，不在路由里重写文案。"""
    raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


def _token(
    authorization: str | None,
    x_user_token: str | None,
) -> str | None:
    return parse_bearer_token(authorization, x_user_token)


def build_auth_router(store: IdentityStore) -> APIRouter:
    """用户管理 HTTP 入口。

    store 必须是 MatrixTaskService.identity 那一份，保证读写同一张 SQLite。
    路径与 static/matrix-auth.js 对齐。本文件只做协议适配。
    """

    router = APIRouter(tags=["auth"])

    @router.post("/api/users/register", response_model=AuthOut, status_code=201)
    async def register_user(command: RegisterIn) -> AuthOut:
        """公开注册并登录，返回 user + 明文 token（库里只存哈希）。"""
        try:
            return await store.register(command.username, command.password)
        except IdentityError as exc:
            _raise_identity(exc)
            raise

    @router.post("/api/users/login", response_model=AuthOut)
    async def login_user(command: LoginIn) -> AuthOut:
        """校验用户名密码，签发新 token。"""
        try:
            return await store.login(command.username, command.password)
        except IdentityError as exc:
            _raise_identity(exc)
            raise

    @router.post("/api/users/logout", status_code=204)
    async def logout_user(
        authorization: str | None = Header(default=None),
        x_user_token: str | None = Header(default=None, alias="X-User-Token"),
    ) -> None:
        """作废当前 token，客户端需清本地凭证并回到登录页。"""
        try:
            await store.logout(_token(authorization, x_user_token))
        except IdentityError as exc:
            _raise_identity(exc)
            raise

    @router.get("/api/users/me", response_model=UserOut)
    async def current_user(
        authorization: str | None = Header(default=None),
        x_user_token: str | None = Header(default=None, alias="X-User-Token"),
    ) -> UserOut:
        """查当前登录用户。优先 X-User-Token，否则 Authorization: Bearer。"""
        try:
            return await store.user_for_token(_token(authorization, x_user_token))
        except IdentityError as exc:
            _raise_identity(exc)
            raise

    @router.get("/api/users", response_model=UserListOut)
    async def list_users(
        authorization: str | None = Header(default=None),
        x_user_token: str | None = Header(default=None, alias="X-User-Token"),
    ) -> UserListOut:
        """登录后列出账号。管理员看全部，普通用户只看自己。"""
        try:
            return await store.list_users(_token(authorization, x_user_token))
        except IdentityError as exc:
            _raise_identity(exc)
            raise

    @router.post("/api/users", response_model=UserOut, status_code=201)
    async def create_user(
        command: CreateUserIn,
        authorization: str | None = Header(default=None),
        x_user_token: str | None = Header(default=None, alias="X-User-Token"),
    ) -> UserOut:
        """管理员新建账号，可指定 role=admin|user；不切换当前登录。"""
        try:
            return await store.create_user(
                _token(authorization, x_user_token),
                command.username,
                command.password,
                command.role,
            )
        except IdentityError as extra:
            _raise_identity(extra)
            raise

    @router.patch("/api/users/{user_id}", response_model=UserOut)
    async def update_user(
        user_id: str,
        command: UpdateUserIn,
        authorization: str | None = Header(default=None),
        x_user_token: str | None = Header(default=None, alias="X-User-Token"),
    ) -> UserOut:
        """改用户名、密码或角色。改他人/改角色仅管理员；改自己必须带 current_password。"""
        try:
            return await store.update_user(
                _token(authorization, x_user_token),
                user_id,
                username=command.username,
                current_password=command.current_password,
                new_password=command.new_password,
                role=command.role,
            )
        except IdentityError as extra:
            _raise_identity(extra)
            raise

    @router.delete("/api/users/{user_id}", status_code=204)
    async def delete_user(
        user_id: str,
        authorization: str | None = Header(default=None),
        x_user_token: str | None = Header(default=None, alias="X-User-Token"),
    ) -> None:
        """管理员删除账号。不能删除最后一个管理员。"""
        try:
            await store.delete_user(_token(authorization, x_user_token), user_id)
        except IdentityError as extra:
            _raise_identity(extra)
            raise

    return router
