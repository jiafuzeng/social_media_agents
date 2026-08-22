"""物化 TikHub 信封 → 推文创作素材卡（对齐 matrix-tikhub-clean，本文件自洽）。"""

from __future__ import annotations

from typing import Any


def _as_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(float(str(value).replace(",", "")))
    except (TypeError, ValueError):
        return default


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _pick_mp4(variants: Any) -> str:
    if not isinstance(variants, list):
        return ""
    best_url = ""
    best_bitrate = -1
    for item in variants:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        if not url:
            continue
        ctype = str(
            item.get("content_type") or item.get("contentType") or ""
        ).lower()
        if "mpegurl" in ctype or "mp4" not in ctype:
            continue
        bitrate = _as_int(item.get("bitrate"), -1)
        if bitrate >= best_bitrate:
            best_bitrate = bitrate
            best_url = url
    return best_url


def _media_items(media: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if media is None:
        return out
    if isinstance(media, list):
        for row in media:
            if not isinstance(row, dict):
                continue
            if row.get("type") in {"photo", "video", "gif"} and (
                row.get("thumb") or row.get("media_url_https")
            ):
                item: dict[str, Any] = {"type": row["type"]}
                thumb = str(row.get("thumb") or row.get("media_url_https") or "")
                if thumb:
                    item["thumb"] = thumb
                if row.get("video_url"):
                    item["video_url"] = row["video_url"]
                if row.get("duration_s") is not None:
                    item["duration_s"] = row["duration_s"]
                out.append(item)
            else:
                out.extend(_media_items({"photo": [row]}))
        return out
    if not isinstance(media, dict):
        return out

    for src, type_name in (("photo", "photo"), ("video", "video"), ("animated_gif", "gif")):
        rows = media.get(src)
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            thumb = str(
                row.get("media_url_https")
                or row.get("thumb")
                or row.get("preview_image_url")
                or ""
            ).strip()
            item = {"type": type_name}
            if thumb:
                item["thumb"] = thumb
            if type_name in {"video", "gif"}:
                mp4 = _pick_mp4(row.get("variants"))
                if mp4:
                    item["video_url"] = mp4
                duration = row.get("duration")
                if duration is not None:
                    ms = _as_int(duration)
                    item["duration_s"] = (
                        round(ms / 1000.0, 1) if ms >= 1000 else float(ms)
                    )
            out.append(item)
    return out


def _media_from_entities(data: dict[str, Any]) -> list[dict[str, Any]]:
    entities = _as_dict(data.get("entities"))
    rows = entities.get("media")
    if not isinstance(rows, list):
        return []
    out: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        type_name = str(row.get("type") or "photo")
        if type_name == "animated_gif":
            type_name = "gif"
        if type_name not in {"photo", "video", "gif"}:
            type_name = "photo"
        thumb = str(row.get("media_url_https") or "").strip()
        item: dict[str, Any] = {"type": type_name}
        if thumb:
            item["thumb"] = thumb
        if type_name in {"video", "gif"}:
            info = _as_dict(row.get("video_info"))
            mp4 = _pick_mp4(info.get("variants") or row.get("variants"))
            if mp4:
                item["video_url"] = mp4
            duration = info.get("duration_millis") or row.get("duration")
            if duration is not None:
                ms = _as_int(duration)
                item["duration_s"] = round(ms / 1000.0, 1) if ms >= 1000 else float(ms)
        out.append(item)
    return out


def _collect_media(data: dict[str, Any]) -> list[dict[str, Any]]:
    return _media_items(data.get("media")) or _media_from_entities(data)


def _tweet_text(data: dict[str, Any]) -> str:
    return str(
        data.get("display_text") or data.get("text") or data.get("full_text") or ""
    ).strip()


def _tweet_id(data: dict[str, Any]) -> str:
    return str(data.get("tweet_id") or data.get("id") or data.get("id_str") or "")


def _author_id(author: Any) -> str:
    if isinstance(author, str):
        return author
    if not isinstance(author, dict):
        return ""
    return str(author.get("rest_id") or author.get("id") or author.get("id_str") or "")


def _author_screen(data: dict[str, Any]) -> str:
    author = _as_dict(data.get("author"))
    user_info = _as_dict(data.get("user_info"))
    for value in (
        author.get("screen_name"),
        data.get("screen_name"),
        user_info.get("screen_name"),
    ):
        if value:
            return str(value).lstrip("@")
    return ""


def _compact_quoted(quoted: Any) -> dict[str, Any] | None:
    if not isinstance(quoted, dict):
        return None
    text = _tweet_text(quoted)
    tid = _tweet_id(quoted)
    if not tid and not text:
        return None
    out: dict[str, Any] = {
        "tweet_id": tid,
        "author": _author_id(quoted.get("author")),
        "text": text,
    }
    media = _collect_media(quoted)
    if media:
        out["media"] = media
    return out


def _retweeted_source(data: dict[str, Any]) -> dict[str, Any] | None:
    for key in ("retweeted_tweet", "retweeted"):
        rt = data.get(key)
        if isinstance(rt, dict) and (
            _tweet_text(rt) or rt.get("media") or rt.get("quoted")
        ):
            return rt
    return None


def _compact_tweet(data: dict[str, Any]) -> dict[str, Any]:
    likes = data.get("likes")
    if likes is None:
        likes = data.get("favorites")
    tweet: dict[str, Any] = {
        "tweet_id": _tweet_id(data),
        "text": _tweet_text(data),
        "created_at": str(data.get("created_at") or ""),
        "views": _as_int(data.get("views")),
        "likes": _as_int(likes),
        "retweets": _as_int(data.get("retweets")),
        "replies": _as_int(data.get("replies")),
        "bookmarks": _as_int(data.get("bookmarks")),
        "lang": str(data.get("lang") or ""),
    }
    screen = _author_screen(data)
    if screen:
        tweet["screen_name"] = screen

    quoted = _compact_quoted(data.get("quoted"))
    own_media = _collect_media(data)
    rt = _retweeted_source(data)

    if rt is not None:
        rt_media = _collect_media(rt)
        if rt_media:
            tweet["retweeted_media"] = rt_media
        if quoted is None:
            quoted = _compact_quoted(rt.get("quoted"))
        if own_media and not str(tweet["text"]).startswith("RT "):
            tweet["media"] = own_media
        elif own_media and not rt_media:
            tweet["media"] = own_media
    elif own_media:
        tweet["media"] = own_media

    if quoted:
        tweet["quoted"] = quoted
    return tweet


def _has_material(tweet: dict[str, Any]) -> bool:
    if str(tweet.get("text") or "").strip():
        return True
    return bool(tweet.get("quoted") or tweet.get("media") or tweet.get("retweeted_media"))


def _fail(tool: str, args: dict[str, Any], error: str) -> dict[str, Any]:
    return {"items": [{"kind": "error", "tool": tool, "args": args, "ok": False, "error": error}]}


def _envelope_parts(
    payload: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]] | None:
    """拆 TikHub 信封 → (params/args, data, result)；失败返回 None。"""
    if not isinstance(payload, dict):
        return None
    result: dict[str, Any] = payload
    args = _as_dict(payload.get("args"))
    if "result" in payload and isinstance(payload["result"], dict):
        result = payload["result"]
    if not args and isinstance(result.get("params"), dict):
        args = dict(result["params"])
    if result.get("code") not in (None, 200):
        return None
    data = result.get("data")
    if not isinstance(data, dict):
        return None
    params = _as_dict(result.get("params")) or args
    return args, data, params


def _iter_timeline_rows(data: dict[str, Any]) -> list[dict[str, Any]]:
    timeline = data.get("timeline")
    if not isinstance(timeline, list):
        return []
    rows: list[dict[str, Any]] = []
    for item in timeline:
        if not isinstance(item, dict):
            continue
        row_type = item.get("type")
        if row_type and row_type not in {"tweet", "TimelineTweet"}:
            continue
        if item.get("sensitive") is True:
            continue
        rows.append(item)
    return rows


def _timeline_tweets(
    *,
    data: dict[str, Any],
    max_tweets: int,
) -> list[dict[str, Any]]:
    """只抽出推文卡（含置顶）；不含 timeline 外壳。"""
    tweets: list[dict[str, Any]] = []
    seen: set[str] = set()

    pinned_raw = data.get("pinned")
    pinned_rows: list[dict[str, Any]] = []
    if isinstance(pinned_raw, dict):
        pinned_rows = [pinned_raw]
    elif isinstance(pinned_raw, list):
        pinned_rows = [row for row in pinned_raw if isinstance(row, dict)]

    for row in [*pinned_rows, *_iter_timeline_rows(data)]:
        tw = _compact_tweet(row)
        if not _has_material(tw):
            continue
        tid = str(tw.get("tweet_id") or "")
        if tid and tid in seen:
            continue
        if tid:
            seen.add(tid)
        tweets.append(tw)
        if len(tweets) >= max_tweets:
            break
    return tweets


# ── public cleaners ──────────────────────────────────────────────────


def clean_fetch_search_timeline(
    payload: dict[str, Any],
    *,
    max_tweets: int = 20,
) -> dict[str, Any]:
    """TikHub `fetch_search_timeline` → `{\"items\":[推文卡...]}`。"""
    parts = _envelope_parts(payload)
    if parts is None:
        return _fail("fetch_search_timeline", {}, "bad_envelope")
    args, data, _params = parts
    tweets = _timeline_tweets(data=data, max_tweets=max(1, int(max_tweets)))
    if not tweets:
        return _fail("fetch_search_timeline", args, "no_tweets")
    return {"items": tweets}


def clean_fetch_trending(
    payload: dict[str, Any],
    *,
    max_trends: int = 10,
) -> dict[str, Any]:
    """TikHub `fetch_trending` → `{\"items\":[trend 卡...]}`。"""
    parts = _envelope_parts(payload)
    if parts is None:
        return _fail("fetch_trending", {}, "bad_envelope")
    args, data, _params = parts
    out: list[dict[str, Any]] = []
    raw = _as_list(data.get("trends"))
    for row in raw:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or "")
        if not name.strip():
            continue
        out.append(
            {
                "kind": "trend",
                "tool": "fetch_trending",
                "args": args,
                "ok": True,
                "error": "",
                "trend": {
                    "name": name,
                    "description": row.get("description"),
                    "context": row.get("context"),
                },
            }
        )
        if len(out) >= max(1, int(max_trends)):
            break
    return {"items": out} if out else _fail("fetch_trending", args, "no_trends")


def clean_fetch_user_post_tweet(
    payload: dict[str, Any],
    *,
    max_tweets: int = 20,
) -> dict[str, Any]:
    parts = _envelope_parts(payload)
    if parts is None:
        return _fail("fetch_user_post_tweet", {}, "bad_envelope")
    args, data, _params = parts
    tweets = _timeline_tweets(data=data, max_tweets=max(1, int(max_tweets)))
    if not tweets:
        return _fail("fetch_user_post_tweet", args, "no_tweets")
    return {"items": tweets}


def clean_fetch_user_media(
    payload: dict[str, Any],
    *,
    max_tweets: int = 20,
) -> dict[str, Any]:
    parts = _envelope_parts(payload)
    if parts is None:
        return _fail("fetch_user_media", {}, "bad_envelope")
    args, data, _params = parts
    tweets = _timeline_tweets(data=data, max_tweets=max(1, int(max_tweets)))
    if not tweets:
        return _fail("fetch_user_media", args, "no_tweets")
    return {"items": tweets}


def clean_fetch_user_profile(payload: dict[str, Any]) -> dict[str, Any]:
    parts = _envelope_parts(payload)
    if parts is None:
        return _fail("fetch_user_profile", {}, "bad_envelope")
    args, data, params = parts
    handle = str(
        data.get("profile") or data.get("screen_name") or params.get("screen_name") or ""
    ).lstrip("@")
    if handle in {"None", "null"}:
        handle = str(params.get("screen_name") or "").lstrip("@")
    pinned = data.get("pinned_tweet_ids_str")
    pinned_ids = [str(x) for x in pinned[:3]] if isinstance(pinned, list) else []
    return {
        "items": [
            {
                "kind": "profile",
                "tool": "fetch_user_profile",
                "args": args,
                "ok": True,
                "error": "",
                "user": {
                    "screen_name": handle,
                    "id": str(data.get("rest_id") or params.get("rest_id") or ""),
                },
                "profile": {
                    "display_name": str(data.get("name") or ""),
                    "bio": str(data.get("desc") or data.get("description") or "").strip(),
                    "followers": _as_int(data.get("sub_count") or data.get("followers")),
                    "following": _as_int(data.get("friends")),
                    "statuses_count": _as_int(data.get("statuses_count")),
                    "media_count": _as_int(data.get("media_count")),
                    "location": str(data.get("location") or ""),
                    "protected": bool(data.get("protected")),
                    "blue_verified": bool(data.get("blue_verified")),
                    "pinned_tweet_ids": pinned_ids,
                },
            }
        ]
    }


def clean_fetch_tweet_detail(payload: dict[str, Any]) -> dict[str, Any]:
    parts = _envelope_parts(payload)
    if parts is None:
        return _fail("fetch_tweet_detail", {}, "bad_envelope")
    args, data, params = parts
    tw = _compact_tweet(data)
    if not tw.get("tweet_id"):
        tw["tweet_id"] = str(params.get("tweet_id") or args.get("tweet_id") or "")
    if not _has_material(tw):
        return _fail("fetch_tweet_detail", args, "no_tweet_content")
    return {
        "items": [
            {
                "kind": "tweet",
                "tool": "fetch_tweet_detail",
                "args": args,
                "ok": True,
                "error": "",
                **tw,
            }
        ]
    }


_CLEANERS: dict[str, Any] = {
    "fetch_tweet_detail": clean_fetch_tweet_detail,
    "fetch_user_profile": clean_fetch_user_profile,
    "fetch_user_post_tweet": clean_fetch_user_post_tweet,
    "fetch_user_media": clean_fetch_user_media,
    "fetch_search_timeline": clean_fetch_search_timeline,
    "fetch_trending": clean_fetch_trending,
}


def materialize_tool_batch(
    batch: list[dict[str, Any]],
    *,
    max_tweets: int = 20,
    max_trends: int = 10,
) -> list[dict[str, Any]]:
    """把 source Act 产出的 tool_batch 物化为素材卡列表。

    每条 batch 项形如 ``{tool, args, result}``；返回扁平 ``items``（可追加进
    ``tool_result_cleaned``）。
    """
    items: list[dict[str, Any]] = []
    for entry in batch:
        if not isinstance(entry, dict):
            continue
        tool = str(entry.get("tool") or "").strip()
        args = _as_dict(entry.get("args"))
        cleaner = _CLEANERS.get(tool)
        if cleaner is None:
            items.append(
                {
                    "kind": "error",
                    "tool": tool,
                    "args": args,
                    "ok": False,
                    "error": f"unsupported_tool:{tool or 'empty'}",
                }
            )
            continue
        result = entry.get("result")
        # Act 异常：{"error": "..."}，无 TikHub data/router
        if (
            isinstance(result, dict)
            and "data" not in result
            and "router" not in result
            and result.get("error")
        ):
            items.append(
                {
                    "kind": "error",
                    "tool": tool,
                    "args": args,
                    "ok": False,
                    "error": str(result.get("error") or "tool_failed"),
                }
            )
            continue
        kwargs: dict[str, Any] = {}
        if tool == "fetch_trending":
            kwargs["max_trends"] = max_trends
        elif tool in {
            "fetch_user_post_tweet",
            "fetch_user_media",
            "fetch_search_timeline",
        }:
            kwargs["max_tweets"] = max_tweets
        try:
            projected = cleaner(entry, **kwargs)
        except TypeError:
            projected = cleaner(entry)
        except Exception as exc:
            items.append(
                {
                    "kind": "error",
                    "tool": tool,
                    "args": args,
                    "ok": False,
                    "error": f"materialize_error:{type(exc).__name__}",
                }
            )
            continue
        rows = projected.get("items") if isinstance(projected, dict) else None
        if isinstance(rows, list) and rows:
            items.extend(row for row in rows if isinstance(row, dict))
        else:
            items.append(
                {
                    "kind": "error",
                    "tool": tool,
                    "args": args,
                    "ok": False,
                    "error": "empty_materialize",
                }
            )
    return items