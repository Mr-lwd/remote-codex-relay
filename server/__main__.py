"""Run a single Relay process using the shared configuration."""

import argparse
import shutil
import sys

from server.config import load_settings


def main():
    parser = argparse.ArgumentParser(description="Codex Relay web console")
    try:
        settings = load_settings()
    except (ValueError, OSError) as error:
        parser.error(f"配置无效：{error}。运行 bash scripts/relay.sh status 查看诊断。")
    parser.add_argument("--host", default=settings.host)
    parser.add_argument("--port", type=int, default=settings.port)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("端口必须在 1–65535 之间。")
    if not shutil.which(settings.codex_bin):
        parser.error(
            "找不到 Codex CLI，请安装或设置 CODEX_BIN；运行 bash scripts/relay.sh status 查看诊断。"
        )
    if not (settings.dist_dir / "index.html").is_file():
        parser.error(
            "前端文件缺失或路径已变更。请在当前项目目录运行 bash scripts/relay.sh repair。"
        )
    import uvicorn

    # Queues, native RPC connections and approval requests are process-local.
    uvicorn.run(
        "server.app:app",
        host=args.host,
        port=args.port,
        workers=1,
        proxy_headers=False,
        timeout_graceful_shutdown=5,
    )


if __name__ == "__main__":
    sys.exit(main())
