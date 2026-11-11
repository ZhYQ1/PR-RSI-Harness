"""PR-RSI Harness Webhook 服务启动脚本。"""

from __future__ import annotations

import os

import uvicorn

from src.webhook.server import create_app


def main() -> None:
    app = create_app()
    host = os.environ.get("WEBHOOK_HOST", "0.0.0.0")
    port = int(os.environ.get("WEBHOOK_PORT", "8080"))
    reload = os.environ.get("WEBHOOK_RELOAD", "false").lower() == "true"
    uvicorn.run(app, host=host, port=port, reload=reload)


if __name__ == "__main__":
    main()