"""Run a single Relay process using the shared configuration."""

import argparse
import shutil
import sys

from server.config import settings


def main():
    parser = argparse.ArgumentParser(description="Codex Relay web console")
    parser.add_argument("--host", default=settings.host)
    parser.add_argument("--port", type=int, default=settings.port)
    args = parser.parse_args()
    if not shutil.which(settings.codex_bin):
        parser.error("Codex CLI not found. Install it or set CODEX_BIN; see README.md.")
    if not (settings.dist_dir / "index.html").is_file():
        parser.error("Frontend missing. Run npm ci && npm run build first.")
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
