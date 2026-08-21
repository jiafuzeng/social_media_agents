import os
from pathlib import Path
from pprint import pprint

from dotenv import load_dotenv
from tikhub import TikHub
from tikhub._errors import TikHubPermissionError

load_dotenv(Path(__file__).resolve().parents[2] / ".env")
client = TikHub(api_key=os.environ["TIKHUB_API_KEY"])

# 1:1 with the OpenAPI spec — resource = tag, method = path basename
# video = client.douyin_web.fetch_one_video(aweme_id="7251234567890123456")
try:
    # country must be PascalCase: China, UnitedStates, ...
    info = client.twitter_web.fetch_trending(country="China")
    trends = info["data"]["trends"]
    print(f"接口 {info['router']}  国家 {info['params']['country']}  共 {len(trends)} 条")
    print(f"状态 {info['code']}  {info['message_zh']}")
    print()
    for i, item in enumerate(trends, start=1):
        print(f"{i:2}. {item['name']}    [{item['context']}]")
except TikHubPermissionError as exc:
    # 403: key is valid, but this Twitter-Web route is not on the account plan/quota
    print(exc)
    pprint(exc.response_body)
finally:
    client.close()
