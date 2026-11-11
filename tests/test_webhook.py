"""Webhook 服务测试：验证签名、幂等、事件过滤等逻辑。"""

from __future__ import annotations

import hashlib
import hmac
import json
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from src.webhook.server import create_app, _idempotency


def _make_github_signature(payload: bytes, secret: str) -> str:
    mac = hmac.new(secret.encode("utf-8"), msg=payload, digestmod=hashlib.sha256)
    return f"sha256={mac.hexdigest()}"


@pytest.fixture(autouse=True)
def reset_idempotency():
    _idempotency.reset()
    yield
    _idempotency.reset()


@pytest.fixture
def client():
    app = create_app(
        db_url=":memory:",
        github_secret="test-secret",
        gitlab_token="test-token",
        default_branch="main",
        default_tenant="default",
    )
    return TestClient(app)


def _github_pr_payload(action: str = "opened", pr_number: int = 1) -> dict:
    return {
        "action": action,
        "pull_request": {
            "number": pr_number,
            "head": {"repo": {"html_url": "https://github.com/owner/repo"}},
        },
        "repository": {"html_url": "https://github.com/owner/repo"},
    }


def _gitlab_mr_payload(action: str = "open", mr_iid: int = 1) -> dict:
    return {
        "object_kind": "merge_request",
        "object_action": action,
        "merge_request": {
            "iid": mr_iid,
            "source_project": {"web_url": "https://gitlab.com/owner/repo"},
        },
        "project": {"web_url": "https://gitlab.com/owner/repo"},
    }


def test_health_endpoint(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_github_webhook_valid_signature(client):
    payload = _github_pr_payload("opened", 1)
    body = json.dumps(payload).encode("utf-8")
    signature = _make_github_signature(body, "test-secret")

    with patch("src.webhook.server._run_pr_review", new_callable=AsyncMock) as mock_review:
        mock_review.return_value = {"task_id": "abc", "status": "DONE", "findings": []}
        resp = client.post(
            "/webhook/github",
            content=body,
            headers={
                "X-Hub-Signature-256": signature,
                "X-GitHub-Event": "pull_request",
                "Content-Type": "application/json",
            },
        )
    assert resp.status_code == 202
    data = resp.json()
    assert data["status"] == "accepted"
    assert data["pr"] == 1
    assert data["repo"] == "https://github.com/owner/repo.git"
    mock_review.assert_called_once()


def test_github_webhook_invalid_signature(client):
    body = json.dumps(_github_pr_payload("opened", 1)).encode("utf-8")
    resp = client.post(
        "/webhook/github",
        content=body,
        headers={
            "X-Hub-Signature-256": "sha256=invalid",
            "X-GitHub-Event": "pull_request",
        },
    )
    assert resp.status_code == 401


def test_github_webhook_wrong_event(client):
    payload = _github_pr_payload("opened", 1)
    body = json.dumps(payload).encode("utf-8")
    signature = _make_github_signature(body, "test-secret")
    resp = client.post(
        "/webhook/github",
        content=body,
        headers={
            "X-Hub-Signature-256": signature,
            "X-GitHub-Event": "push",
        },
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "ignored"


def test_github_webhook_unsupported_action(client):
    payload = _github_pr_payload("closed", 1)
    body = json.dumps(payload).encode("utf-8")
    signature = _make_github_signature(body, "test-secret")
    resp = client.post(
        "/webhook/github",
        content=body,
        headers={
            "X-Hub-Signature-256": signature,
            "X-GitHub-Event": "pull_request",
        },
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "ignored"


def test_github_webhook_idempotency(client):
    payload = _github_pr_payload("opened", 1)
    body = json.dumps(payload).encode("utf-8")
    signature = _make_github_signature(body, "test-secret")

    headers = {
        "X-Hub-Signature-256": signature,
        "X-GitHub-Event": "pull_request",
        "Content-Type": "application/json",
    }

    with patch("src.webhook.server._run_pr_review", new_callable=AsyncMock):
        resp1 = client.post("/webhook/github", content=body, headers=headers)
    assert resp1.status_code == 202

    with patch("src.webhook.server._run_pr_review", new_callable=AsyncMock) as mock_review:
        resp2 = client.post("/webhook/github", content=body, headers=headers)
    assert resp2.status_code == 200
    assert resp2.json()["status"] == "ignored"
    assert resp2.json()["reason"] == "duplicate event"
    mock_review.assert_not_called()


def test_gitlab_webhook_valid_token(client):
    payload = _gitlab_mr_payload("open", 1)
    body = json.dumps(payload).encode("utf-8")

    with patch("src.webhook.server._run_pr_review", new_callable=AsyncMock) as mock_review:
        mock_review.return_value = {"task_id": "abc", "status": "DONE", "findings": []}
        resp = client.post(
            "/webhook/gitlab",
            content=body,
            headers={
                "X-Gitlab-Token": "test-token",
                "X-Gitlab-Event": "Merge Request Hook",
                "Content-Type": "application/json",
            },
        )
    assert resp.status_code == 202
    data = resp.json()
    assert data["status"] == "accepted"
    assert data["mr"] == 1
    assert data["repo"] == "https://gitlab.com/owner/repo.git"
    mock_review.assert_called_once()


def test_gitlab_webhook_invalid_token(client):
    body = json.dumps(_gitlab_mr_payload("open", 1)).encode("utf-8")
    resp = client.post(
        "/webhook/gitlab",
        content=body,
        headers={
            "X-Gitlab-Token": "wrong-token",
            "X-Gitlab-Event": "Merge Request Hook",
        },
    )
    assert resp.status_code == 401


def test_gitlab_webhook_wrong_event(client):
    payload = _gitlab_mr_payload("open", 1)
    body = json.dumps(payload).encode("utf-8")
    resp = client.post(
        "/webhook/gitlab",
        content=body,
        headers={
            "X-Gitlab-Token": "test-token",
            "X-Gitlab-Event": "Issue Hook",
        },
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "ignored"


def test_idempotency_checker():
    from src.webhook.verify import IdempotencyChecker

    checker = IdempotencyChecker(ttl_seconds=1)
    assert checker.is_duplicate("event-1") is False
    assert checker.is_duplicate("event-1") is True
    assert checker.is_duplicate("event-2") is False

    import time
    time.sleep(1.1)
    assert checker.is_duplicate("event-1") is False