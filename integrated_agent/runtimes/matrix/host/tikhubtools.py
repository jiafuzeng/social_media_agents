"""TikHub Twitter-Web API → Agently 可调用工具。

OpenAPI: https://api.tikhub.io/#/Twitter-Web-API
方法名 = `/api/v1/twitter/web/` 最后一段；全部 GET query。

用法（原生 Function Calling）::

    from integrated_agent.runtimes.matrix.host.tikhubtools import TWITTER_WEB_TOOL_FUNCS

    agent = Agently.create_agent()
    agent.input(...).use_tools(TWITTER_WEB_TOOL_FUNCS).get_response()

用法（s03 形 ReAct 字典）::

    from integrated_agent.runtimes.matrix.host.tikhubtools import TWITTER_WEB_TOOLS

    tools_desc = [
        {"name": k, "desc": v["desc"], "args": v["args"]} for k, v in TWITTER_WEB_TOOLS.items()
    ]
    result = TWITTER_WEB_TOOLS[name]["func"](**args)
"""

from __future__ import annotations

import asyncio
import os
from typing import Annotated, Any, Callable

from tikhub import TikHub
from tikhub._errors import TikHubPermissionError

# ---------------------------------------------------------------------------
# 底层：每跳 new TikHub → 打一次 → finally close()（禁止跨跳复用 client）
# ---------------------------------------------------------------------------


def _api_key() -> str:
    key = (os.environ.get("TIKHUB_API_KEY") or "").strip()
    if not key:
        raise RuntimeError("TIKHUB_API_KEY is not set")
    return key


def _call_twitter_web(method: str, **params: Any) -> Any:
    """同步打一次 Twitter-Web；参数 None 的字段不传。"""
    cleaned = {k: v for k, v in params.items() if v is not None}
    client = TikHub(api_key=_api_key())
    try:
        fn = getattr(client.twitter_web, method)
        return fn(**cleaned)
    except TikHubPermissionError as exc:
        return {
            "ok": False,
            "error": "tikhub_permission",
            "method": method,
            "detail": str(exc),
            "response_body": getattr(exc, "response_body", None),
        }
    finally:
        client.close()


async def _acall_twitter_web(method: str, **params: Any) -> Any:
    """异步封装：线程里同步 HTTP。"""
    return await asyncio.to_thread(_call_twitter_web, method, **params)


# ---------------------------------------------------------------------------
# Agent 工具（Annotated + docstring → Agently tool schema）
# ---------------------------------------------------------------------------


async def fetch_tweet_detail(
    tweet_id: Annotated[str, "推文 ID，如从 https://x.com/{user}/status/{id} 取出的数字 id"],
) -> Any:
    """获取单条推文详情（正文、媒体、互动数等）。"""
    return await _acall_twitter_web("fetch_tweet_detail", tweet_id=tweet_id)


async def fetch_user_profile(
    screen_name: Annotated[
        str | None, "用户名（不含 @）；与 rest_id 二选一，有 rest_id 时优先用 rest_id"
    ] = None,
    rest_id: Annotated[int | None, "用户数字 rest_id；与 screen_name 二选一"] = None,
) -> Any:
    """获取用户资料（简介、粉丝数等）。screen_name 与 rest_id 二选一。"""
    return await _acall_twitter_web(
        "fetch_user_profile", screen_name=screen_name, rest_id=rest_id
    )


async def fetch_user_post_tweet(
    screen_name: Annotated[
        str | None, "用户名（不含 @）；与 rest_id 二选一"
    ] = None,
    rest_id: Annotated[int | None, "用户数字 rest_id；有则不要再传 screen_name"] = None,
    cursor: Annotated[str | None, "翻页游标；首页传 None"] = None,
) -> Any:
    """获取用户发帖时间线。首页 cursor=None。"""
    return await _acall_twitter_web(
        "fetch_user_post_tweet",
        screen_name=screen_name,
        rest_id=rest_id,
        cursor=cursor,
    )


async def fetch_user_media(
    screen_name: Annotated[str, "用户名（不含 @），必填"],
    rest_id: Annotated[int | None, "可选 rest_id"] = None,
    cursor: Annotated[str | None, "翻页游标；首页传 None"] = None,
) -> Any:
    """获取用户媒体时间线（图/视频帖）。不能按 tweet_id 查。"""
    return await _acall_twitter_web(
        "fetch_user_media",
        screen_name=screen_name,
        rest_id=rest_id,
        cursor=cursor,
    )


async def fetch_search_timeline(
    keyword: Annotated[str, "搜索关键词，必填；不要传整段 URL"],
    search_type: Annotated[
        str | None,
        "搜索类型：Latest（默认）/ Top / Media / People / Lists；P0 常用 Latest",
    ] = None,
    cursor: Annotated[str | None, "翻页游标；首页传 None"] = None,
) -> Any:
    """按关键字搜索推文时间线。"""
    return await _acall_twitter_web(
        "fetch_search_timeline",
        keyword=keyword,
        search_type=search_type,
        cursor=cursor,
    )


async def fetch_trending(
    country: Annotated[
        str | None,
        "国家/地区，PascalCase，如 China、UnitedStates；省略时由上游决定（产品侧常用 China）",
    ] = None,
) -> Any:
    """获取 Twitter/X 热搜趋势列表。"""
    return await _acall_twitter_web("fetch_trending", country=country)


async def fetch_post_comments(
    tweet_id: Annotated[str, "推文 ID"],
    cursor: Annotated[str | None, "翻页游标；首页传 None"] = None,
) -> Any:
    """获取推文评论。"""
    return await _acall_twitter_web(
        "fetch_post_comments", tweet_id=tweet_id, cursor=cursor
    )


async def fetch_latest_post_comments(
    tweet_id: Annotated[str, "推文 ID"],
    cursor: Annotated[str | None, "翻页游标；首页传 None"] = None,
) -> Any:
    """获取推文最新评论。"""
    return await _acall_twitter_web(
        "fetch_latest_post_comments", tweet_id=tweet_id, cursor=cursor
    )


async def fetch_user_tweet_replies(
    screen_name: Annotated[str, "用户名（不含 @）"],
    cursor: Annotated[str | None, "翻页游标；首页传 None"] = None,
) -> Any:
    """获取用户发出的回复时间线。"""
    return await _acall_twitter_web(
        "fetch_user_tweet_replies", screen_name=screen_name, cursor=cursor
    )


async def fetch_user_followers(
    screen_name: Annotated[str, "用户名（不含 @）"],
    cursor: Annotated[str | None, "翻页游标；首页传 None"] = None,
) -> Any:
    """获取用户粉丝列表。"""
    return await _acall_twitter_web(
        "fetch_user_followers", screen_name=screen_name, cursor=cursor
    )


async def fetch_user_followings(
    screen_name: Annotated[str, "用户名（不含 @）"],
    cursor: Annotated[str | None, "翻页游标；首页传 None"] = None,
) -> Any:
    """获取用户关注列表。"""
    return await _acall_twitter_web(
        "fetch_user_followings", screen_name=screen_name, cursor=cursor
    )


async def fetch_retweet_user_list(
    tweet_id: Annotated[str, "推文 ID"],
    cursor: Annotated[str | None, "翻页游标；首页传 None"] = None,
) -> Any:
    """获取转推该帖的用户列表。"""
    return await _acall_twitter_web(
        "fetch_retweet_user_list", tweet_id=tweet_id, cursor=cursor
    )


# 供 agent.use_tools([...]) 一次挂上
TWITTER_WEB_TOOL_FUNCS: list[Callable[..., Any]] = [
    fetch_tweet_detail,
    fetch_user_profile,
    fetch_user_post_tweet,
    fetch_user_media,
    fetch_search_timeline,
    fetch_trending,
    fetch_post_comments,
    fetch_latest_post_comments,
    fetch_user_tweet_replies,
    fetch_user_followers,
    fetch_user_followings,
    fetch_retweet_user_list,
]

# 写帖常用子集（创作 / 改写）；评论与粉丝类留给回评
COMPOSE_TOOL_FUNCS: list[Callable[..., Any]] = [
    fetch_tweet_detail,
    fetch_user_profile,
    fetch_user_post_tweet,
    fetch_user_media,
    fetch_search_timeline,
    fetch_trending,
]

REPLY_TOOL_FUNCS: list[Callable[..., Any]] = [
    fetch_post_comments,
    fetch_latest_post_comments,
    fetch_user_tweet_replies,
]


def _sync_call(method: str, **params: Any) -> Any:
    """s03 ReAct 同步入口。"""
    return _call_twitter_web(method, **params)


# s03 / 自建 ReAct：{"name": {desc, args, func}}
TWITTER_WEB_TOOLS: dict[str, dict[str, Any]] = {
    "fetch_tweet_detail": {
        "desc": "获取单条推文详情（正文、媒体、互动数等）",
        "args": {"tweet_id": "推文 ID（status/{id}）"},
        "func": lambda **kw: _sync_call("fetch_tweet_detail", **kw),
    },
    "fetch_user_profile": {
        "desc": "获取用户资料；screen_name 与 rest_id 二选一",
        "args": {
            "screen_name": "用户名（不含 @），可选",
            "rest_id": "用户数字 id，可选",
        },
        "func": lambda **kw: _sync_call("fetch_user_profile", **kw),
    },
    "fetch_user_post_tweet": {
        "desc": "获取用户发帖时间线；首页 cursor=None",
        "args": {
            "screen_name": "用户名，可选",
            "rest_id": "用户数字 id，可选",
            "cursor": "翻页游标，首页 None",
        },
        "func": lambda **kw: _sync_call("fetch_user_post_tweet", **kw),
    },
    "fetch_user_media": {
        "desc": "获取用户媒体时间线；不能按 tweet_id 查",
        "args": {
            "screen_name": "用户名，必填",
            "rest_id": "可选 rest_id",
            "cursor": "翻页游标，首页 None",
        },
        "func": lambda **kw: _sync_call("fetch_user_media", **kw),
    },
    "fetch_search_timeline": {
        "desc": "按关键字搜索推文；缺省 search_type=Latest",
        "args": {
            "keyword": "搜索关键词，必填",
            "search_type": "Latest/Top/Media/People/Lists，可选",
            "cursor": "翻页游标，首页 None",
        },
        "func": lambda **kw: _sync_call("fetch_search_timeline", **kw),
    },
    "fetch_trending": {
        "desc": "获取热搜趋势；country 用 PascalCase 如 China",
        "args": {"country": "国家/地区，如 China；可选"},
        "func": lambda **kw: _sync_call("fetch_trending", **kw),
    },
    "fetch_post_comments": {
        "desc": "获取推文评论",
        "args": {"tweet_id": "推文 ID", "cursor": "翻页游标，首页 None"},
        "func": lambda **kw: _sync_call("fetch_post_comments", **kw),
    },
    "fetch_latest_post_comments": {
        "desc": "获取推文最新评论",
        "args": {"tweet_id": "推文 ID", "cursor": "翻页游标，首页 None"},
        "func": lambda **kw: _sync_call("fetch_latest_post_comments", **kw),
    },
    "fetch_user_tweet_replies": {
        "desc": "获取用户发出的回复",
        "args": {"screen_name": "用户名", "cursor": "翻页游标，首页 None"},
        "func": lambda **kw: _sync_call("fetch_user_tweet_replies", **kw),
    },
    "fetch_user_followers": {
        "desc": "获取用户粉丝列表",
        "args": {"screen_name": "用户名", "cursor": "翻页游标，首页 None"},
        "func": lambda **kw: _sync_call("fetch_user_followers", **kw),
    },
    "fetch_user_followings": {
        "desc": "获取用户关注列表",
        "args": {"screen_name": "用户名", "cursor": "翻页游标，首页 None"},
        "func": lambda **kw: _sync_call("fetch_user_followings", **kw),
    },
    "fetch_retweet_user_list": {
        "desc": "获取转推该帖的用户列表",
        "args": {"tweet_id": "推文 ID", "cursor": "翻页游标，首页 None"},
        "func": lambda **kw: _sync_call("fetch_retweet_user_list", **kw),
    },
}


def register_twitter_web_tools(agent: Any, *, funcs: list[Callable[..., Any]] | None = None) -> list[str]:
    """把工具挂到指定 agent，返回已注册工具名。

    等价于 ``agent.use_tools(funcs, always=True)`` 的注册副作用；
    之后仍需在 execution 上 ``.use_tools(...)`` 才会暴露给模型。
    """
    chosen = funcs if funcs is not None else TWITTER_WEB_TOOL_FUNCS
    names: list[str] = []
    for fn in chosen:
        agent.tool_func(fn)
        names.append(fn.__name__)
    return names


__all__ = [
    "COMPOSE_TOOL_FUNCS",
    "REPLY_TOOL_FUNCS",
    "TWITTER_WEB_TOOL_FUNCS",
    "TWITTER_WEB_TOOLS",
    "fetch_latest_post_comments",
    "fetch_post_comments",
    "fetch_retweet_user_list",
    "fetch_search_timeline",
    "fetch_trending",
    "fetch_tweet_detail",
    "fetch_user_followers",
    "fetch_user_followings",
    "fetch_user_media",
    "fetch_user_post_tweet",
    "fetch_user_profile",
    "fetch_user_tweet_replies",
    "register_twitter_web_tools",
]
