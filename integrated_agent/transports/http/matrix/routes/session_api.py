from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException

from integrated_agent.runtimes.matrix.host.identity import (
    CreateSessionIn,
    IdentityError,
    IdentityStore,
    SessionListOut,
    SessionOut,
    UpdateSessionIn,
    parse_bearer_token,
)


def _raise_identity(error: IdentityError) -> None:
    """把 IdentityStore 的业务错误转成 HTTP 状态码，不在路由里重写文案。"""
    raise HTTPException(status_code=error.status, detail=str(error)) from error


def _token(
    authorization: str | None,
    x_user_token: str | None,
) -> str | None:
    """优先 X-User-Token，否则 Authorization: Bearer。"""
    return parse_bearer_token(authorization, x_user_token)


def build_session_router(store: IdentityStore) -> APIRouter:
    """矩阵工作台「对话会话」HTTP 入口。

    store 必须是 MatrixTaskService.identity 那一份，保证与登录用户读写同一张 SQLite。
    session_id 由 host 签发，同时作为 Agently Session.id；本文件只做会话 CRUD 协议适配。
    路径与 static/matrix.js 左侧历史对齐。问数、企微不走这些路径。
    用户轮次由 /api/create、/api/reply 在受理任务时 append_turn，不在这里写入。
    """

    router = APIRouter(tags=["matrix-session"])

    @router.get("/api/sessions", response_model=SessionListOut)
    async def list_sessions(
        authorization: str | None = Header(default=None),
        x_user_token: str | None = Header(default=None, alias="X-User-Token"),
    ) -> SessionListOut:
        """列出当前登录用户自己的会话，按最近活动排序。"""
        try:
            return await store.list_sessions(_token(authorization, x_user_token))
        except IdentityError as error:
            _raise_identity(error)
            raise

    @router.post("/api/sessions", response_model=SessionOut, status_code=201)
    async def create_session(
        command: CreateSessionIn,
        authorization: str | None = Header(default=None),
        x_user_token: str | None = Header(default=None, alias="X-User-Token"),
    ) -> SessionOut:
        """新建空会话（默认标题「新会话」），尚无 turns。"""
        try:
            return await store.create_session(
                _token(authorization, x_user_token),
                title=command.title,
                last_scenario=command.last_scenario,
            )
        except IdentityError as error:
            _raise_identity(error)
            raise

    @router.get("/api/sessions/{session_id}", response_model=SessionOut)
    async def get_session(
        session_id: str,
        authorization: str | None = Header(default=None),
        x_user_token: str | None = Header(default=None, alias="X-User-Token"),
    ) -> SessionOut:
        """打开自己的会话并带回 turns；别人的会话 403。"""
        try:
            return await store.get_session(
                _token(authorization, x_user_token), session_id
            )
        except IdentityError as error:
            _raise_identity(error)
            raise

    @router.patch("/api/sessions/{session_id}", response_model=SessionOut)
    async def update_session(
        session_id: str,
        command: UpdateSessionIn,
        authorization: str | None = Header(default=None),
        x_user_token: str | None = Header(default=None, alias="X-User-Token"),
    ) -> SessionOut:
        """改标题或归档（status=active|archived）。"""
        try:
            return await store.update_session(
                _token(authorization, x_user_token),
                session_id,
                title=command.title,
                status=command.status,
            )
        except IdentityError as error:
            _raise_identity(error)
            raise

    @router.delete("/api/sessions/{session_id}", status_code=204)
    async def delete_session(
        session_id: str,
        authorization: str | None = Header(default=None),
        x_user_token: str | None = Header(default=None, alias="X-User-Token"),
    ) -> None:
        """删除自己的会话；turns 随外键 CASCADE 一起删。"""
        try:
            await store.delete_session(
                _token(authorization, x_user_token), session_id
            )
        except IdentityError as error:
            _raise_identity(error)
            raise

    return router
