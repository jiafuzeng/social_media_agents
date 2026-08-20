from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException

from integrated_agent.runtimes.matrix.host.identity import (
    AddCollectionItemsIn,
    AddCollectionItemsOut,
    CollectionListOut,
    CollectionOut,
    CreateCollectionIn,
    IdentityError,
    IdentityStore,
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


def build_collection_router(store: IdentityStore) -> APIRouter:
    """矩阵工作台「收藏夹」HTTP 入口。

    store 必须是 MatrixTaskService.identity 那一份，保证与登录用户读写同一张 SQLite。
    路径与 static/matrix.js 右侧收藏夹对齐。问数、企微不走这些路径。
    本文件只做协议适配。
    """

    router = APIRouter(tags=["matrix-collection"])

    @router.get("/api/collections", response_model=CollectionListOut)
    async def list_collections(
        authorization: str | None = Header(default=None),
        x_user_token: str | None = Header(default=None, alias="X-User-Token"),
    ) -> CollectionListOut:
        """列出当前登录用户的收藏夹，含推文与嵌套回复。"""
        try:
            return await store.list_collections(_token(authorization, x_user_token))
        except IdentityError as error:
            _raise_identity(error)
            raise

    @router.post("/api/collections", response_model=CollectionOut, status_code=201)
    async def create_collection(
        command: CreateCollectionIn,
        authorization: str | None = Header(default=None),
        x_user_token: str | None = Header(default=None, alias="X-User-Token"),
    ) -> CollectionOut:
        """新建空收藏夹。同一用户下名称不可重复。"""
        try:
            return await store.create_collection(
                _token(authorization, x_user_token), command.name
            )
        except IdentityError as error:
            _raise_identity(error)
            raise

    @router.get("/api/collections/{collection_id}", response_model=CollectionOut)
    async def get_collection(
        collection_id: str,
        authorization: str | None = Header(default=None),
        x_user_token: str | None = Header(default=None, alias="X-User-Token"),
    ) -> CollectionOut:
        """打开自己的收藏夹并带回条目；别人的收藏夹 403。"""
        try:
            return await store.get_collection(
                _token(authorization, x_user_token), collection_id
            )
        except IdentityError as error:
            _raise_identity(error)
            raise

    @router.delete("/api/collections/{collection_id}", status_code=204)
    async def delete_collection(
        collection_id: str,
        authorization: str | None = Header(default=None),
        x_user_token: str | None = Header(default=None, alias="X-User-Token"),
    ) -> None:
        """删除自己的收藏夹；条目随外键 CASCADE 一起删。"""
        try:
            await store.delete_collection(
                _token(authorization, x_user_token), collection_id
            )
        except IdentityError as error:
            _raise_identity(error)
            raise

    @router.post(
        "/api/collections/{collection_id}/items",
        response_model=AddCollectionItemsOut,
        status_code=201,
    )
    async def add_collection_items(
        collection_id: str,
        command: AddCollectionItemsIn,
        authorization: str | None = Header(default=None),
        x_user_token: str | None = Header(default=None, alias="X-User-Token"),
    ) -> AddCollectionItemsOut:
        """存入推文，或把回复绑到原推（bind_replies=true）。"""
        try:
            return await store.add_collection_items(
                _token(authorization, x_user_token),
                collection_id,
                command.items,
                bind_replies=command.bind_replies,
            )
        except IdentityError as error:
            _raise_identity(error)
            raise

    @router.delete(
        "/api/collections/{collection_id}/items/{item_id}",
        status_code=204,
    )
    async def delete_collection_item(
        collection_id: str,
        item_id: str,
        authorization: str | None = Header(default=None),
        x_user_token: str | None = Header(default=None, alias="X-User-Token"),
    ) -> None:
        """移出一条推文或回复；删推文时级联删其下回复。"""
        try:
            await store.delete_collection_item(
                _token(authorization, x_user_token), collection_id, item_id
            )
        except IdentityError as error:
            _raise_identity(error)
            raise

    return router
