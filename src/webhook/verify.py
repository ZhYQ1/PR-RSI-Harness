"""Webhook 签名验证与幂等控制。

- GitHub：验证 ``X-Hub-Signature-256``（HMAC SHA256，密钥为 ``WEBHOOK_SECRET``）
- GitLab：验证 ``X-Gitlab-Token`` 请求头
- 幂等：内存集合记录已处理的事件 ID，避免重复触发同一 PR 的审查
"""

from __future__ import annotations

import hashlib
import hmac
import time
from typing import Set


class IdempotencyChecker:
    """基于内存的幂等检查器，记录已处理的事件 ID 及其过期时间。"""

    def __init__(self, ttl_seconds: int = 3600) -> None:
        self._ttl = ttl_seconds
        self._seen: dict[str, float] = {}

    def is_duplicate(self, event_id: str) -> bool:
        """如果事件 ID 已在 TTL 内出现过，返回 True；否则记录并返回 False。"""
        now = time.time()
        # 清理过期条目
        expired = [k for k, v in self._seen.items() if v < now]
        for k in expired:
            self._seen.pop(k, None)

        if event_id in self._seen:
            return True
        self._seen[event_id] = now + self._ttl
        return False

    def reset(self) -> None:
        self._seen.clear()


def verify_github_signature(payload: bytes, signature_header: str | None, secret: str) -> bool:
    """验证 GitHub Webhook 签名。

    GitHub 使用 ``X-Hub-Signature-256``，值为 ``sha256=<hex_digest>``。
    """
    if not signature_header or not secret:
        return False

    try:
        algo, expected_sig = signature_header.split("=", 1)
    except ValueError:
        return False

    if algo.lower() != "sha256":
        return False

    mac = hmac.new(secret.encode("utf-8"), msg=payload, digestmod=hashlib.sha256)
    return hmac.compare_digest(mac.hexdigest(), expected_sig)


def verify_gitlab_token(token_header: str | None, secret: str) -> bool:
    """验证 GitLab Webhook 令牌。"""
    if not token_header or not secret:
        return False
    return hmac.compare_digest(token_header, secret)