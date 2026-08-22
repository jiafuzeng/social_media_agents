"""跑 compose.materialize 接口，打印物化结果（fixtures = tests/clean-skill/fetch_*.json）。"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parents[1]
MATERIALIZE_PATH = (
    PROJECT_ROOT / "integrated_agent" / "runtimes" / "matrix" / "compose" / "materialize.py"
)

_spec = importlib.util.spec_from_file_location("matrix_compose_materialize", MATERIALIZE_PATH)
materialize = importlib.util.module_from_spec(_spec)  # type: ignore[arg-type]
sys.modules[_spec.name] = materialize  # type: ignore[union-attr]
_spec.loader.exec_module(materialize)  # type: ignore[union-attr]

clean_fetch_search_timeline = materialize.clean_fetch_search_timeline
clean_fetch_trending = materialize.clean_fetch_trending
clean_fetch_user_post_tweet = materialize.clean_fetch_user_post_tweet
clean_fetch_user_media = materialize.clean_fetch_user_media
clean_fetch_user_profile = materialize.clean_fetch_user_profile
clean_fetch_tweet_detail = materialize.clean_fetch_tweet_detail
materialize_tool_batch = materialize.materialize_tool_batch


def _load(name: str) -> dict[str, Any]:
    return json.loads((HERE / name).read_text(encoding="utf-8"))


def _dump(title: str, payload: Any) -> None:
    print(f"===== {title} =====")
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    print()


def main() -> None:
    search = _load("fetch_search_timeline.json")
    post = _load("fetch_user_post_tweet.json")
    media = _load("fetch_user_media.json")
    detail = _load("fetch_tweet_detail.json")
    profile = _load("fetch_user_profile.json")
    trending = _load("fetch_trending.json")

    _dump("clean_fetch_search_timeline", clean_fetch_search_timeline(search))
    _dump("clean_fetch_user_post_tweet", clean_fetch_user_post_tweet(post))
    _dump("clean_fetch_user_media", clean_fetch_user_media(media))
    _dump("clean_fetch_tweet_detail", clean_fetch_tweet_detail(detail))
    _dump("clean_fetch_user_profile", clean_fetch_user_profile(profile))
    _dump("clean_fetch_trending", clean_fetch_trending(trending))

    batch = [
        {"tool": "fetch_search_timeline", "args": {"keyword": "特朗普事件"}, "result": search},
        {"tool": "fetch_user_post_tweet", "args": {}, "result": post},
        {"tool": "fetch_user_media", "args": {}, "result": media},
        {"tool": "fetch_tweet_detail", "args": {}, "result": detail},
        {"tool": "fetch_user_profile", "args": {}, "result": profile},
        {"tool": "fetch_trending", "args": {}, "result": trending},
    ]
    _dump("materialize_tool_batch", materialize_tool_batch(batch))


if __name__ == "__main__":
    main()
