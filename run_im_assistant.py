"""企业微信综合智能助理进程入口。

组装 Gateway、三种运行时与 WeCom Transport，并阻塞运行 WebSocket 客户端。
"""

from integrated_agent.bootstrap.im_assistant import build_im_assistant


def main() -> None:
    """构建并启动企业微信助理。"""
    build_im_assistant().run()


if __name__ == "__main__":
    main()
