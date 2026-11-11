"""Webhook 服务模块：接收 GitHub / GitLab PR 事件并自动触发 PR-RSI Harness 风险检测。"""

from src.webhook.server import create_app

__all__ = ["create_app"]