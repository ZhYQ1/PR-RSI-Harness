"""Webhook HTTP 服务：接收 GitHub / GitLab PR 事件，自动触发 PR 风险检测。

端点
----
- ``POST /webhook/github`` 接收 GitHub Webhook（需 ``X-Hub-Signature-256`` 签名验证）
- ``POST /webhook/gitlab`` 接收 GitLab Webhook（需 ``X-Gitlab-Token`` 令牌验证）
- ``GET /health`` 健康检查
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any

import httpx

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from src.git.ingestion import extract_pr_diff
from src.harness.runtime import HarnessRuntime
from src.memory.store import MemoryManager
from src.storage.backend import StorageBackend
from src.storage.factory import open_store
from src.webhook.verify import IdempotencyChecker, verify_gitlab_token, verify_github_signature

logger = logging.getLogger("pr-harness.webhook")

GITHUB_SUPPORTED_ACTIONS = {"opened", "synchronize", "reopened"}
GITLAB_SUPPORTED_ACTIONS = {"open", "update", "reopen"}

_idempotency = IdempotencyChecker(ttl_seconds=3600)


def _to_git_url(html_url: str) -> str:
    """将网页 URL 转为可克隆的 Git URL（补充 .git 后缀）。"""
    if not html_url:
        return ""
    html_url = html_url.rstrip("/")
    if not html_url.endswith(".git"):
        html_url += ".git"
    return html_url


def _extract_github_pr_info(payload: dict[str, Any]) -> tuple[str, int, str] | None:
    """从 GitHub Webhook Payload 提取 (repo_url, pr_number, action)。"""
    action = payload.get("action")
    if action not in GITHUB_SUPPORTED_ACTIONS:
        return None

    pr = payload.get("pull_request", {})
    pr_number = pr.get("number")
    if pr_number is None:
        return None

    repo_info = payload.get("repository", {}) or {}
    repo_html_url = repo_info.get("html_url", "")
    if not repo_html_url:
        head_repo = pr.get("head", {}).get("repo", {}) or {}
        repo_html_url = head_repo.get("html_url", "")

    repo_url = _to_git_url(repo_html_url)
    if not repo_url:
        return None

    return repo_url, int(pr_number), action


def _extract_gitlab_mr_info(payload: dict[str, Any]) -> tuple[str, int, str] | None:
    """从 GitLab Webhook Payload 提取 (repo_url, mr_iid, action)。"""
    object_kind = payload.get("object_kind")
    if object_kind != "merge_request":
        return None

    action = payload.get("object_action")
    if action not in GITLAB_SUPPORTED_ACTIONS:
        return None

    mr = payload.get("merge_request", {})
    mr_iid = mr.get("iid")
    if mr_iid is None:
        return None

    project = payload.get("project", {}) or {}
    repo_html_url = project.get("web_url", "")
    if not repo_html_url:
        source = mr.get("source_project", {}) or {}
        repo_html_url = source.get("web_url", "")

    repo_url = _to_git_url(repo_html_url)
    if not repo_url:
        return None

    return repo_url, int(mr_iid), action


async def _run_pr_review(
    repo_url: str,
    pr_number: int,
    branch: str,
    tenant: str,
    db_url: str,
    token: str | None,
) -> dict[str, Any]:
    """后台任务：从 Git 仓库提取 diff 并运行 Harness 风险检测。"""
    loop = asyncio.get_event_loop()

    def _sync_run() -> dict[str, Any]:
        try:
            diff = extract_pr_diff(
                repo_url=repo_url,
                pr_number=pr_number,
                branch=branch,
                token=token,
                shallow=True,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Failed to extract PR diff for %s#%s", repo_url, pr_number)
            return {"error": f"diff_extraction_failed: {exc}"}

        try:
            store: StorageBackend = open_store(db_url)
            runtime = HarnessRuntime(store)
            from src.agent.pipeline import ReviewPipeline

            pipeline = ReviewPipeline(memory=MemoryManager(store))
            outcome = runtime.submit_and_run(
                repo=repo_url,
                pr_number=pr_number,
                branch=branch,
                diff=diff,
                pipeline=pipeline,
                tenant=tenant,
            )
            findings = outcome.result.findings if outcome.result else []
            patch = outcome.result.patch if outcome.result else None

            # 结果回写：在 PR/MR 下发布评论
            review_result = {
                "task_id": outcome.task.id,
                "status": outcome.task.status.value,
                "findings": findings,
                "error": outcome.error,
            }

            comment_body = _build_github_comment(findings, patch, outcome.task.id)

            if token:
                if "github.com" in repo_url:
                    _post_github_pr_comment(repo_url, pr_number, comment_body, token)
                elif "gitlab.com" in repo_url:
                    _post_gitlab_mr_comment(repo_url, pr_number, comment_body, token)

            return review_result
        except Exception as exc:  # noqa: BLE001
            logger.exception("Harness review failed for %s#%s", repo_url, pr_number)
            return {"error": f"review_failed: {exc}"}

    return await loop.run_in_executor(None, _sync_run)


def _build_github_comment(findings: list[str], patch: str | None, task_id: str) -> str:
    """构建 GitHub PR 评论内容。"""
    lines = [
        "## 🛡️ PR-RSI Harness 风险检测报告",
        f"任务 ID: `{task_id}`",
        "",
    ]
    if findings:
        lines.append(f"检测到 **{len(findings)}** 个安全风险：")
        for f in findings:
            lines.append(f"- ⚠️ `{f}`")
        lines.append("")
    else:
        lines.append("✅ 未检测到安全风险。")
        lines.append("")

    if patch:
        lines.append("### 建议修复")
        lines.append("```")
        lines.append(patch[:2000])
        lines.append("```")
        lines.append("")

    lines.append("> 自动生成，由 PR-RSI Harness Webhook 服务触发。")
    return "\n".join(lines)


def _build_gitlab_comment(findings: list[str], patch: str | None, task_id: str) -> str:
    """构建 GitLab MR 评论内容。"""
    return _build_github_comment(findings, patch, task_id)


def _post_github_pr_comment(
    repo_url: str,
    pr_number: int,
    comment_body: str,
    token: str | None,
) -> None:
    """在 GitHub PR 下发布评论。"""
    if not token:
        logger.warning("未配置 GITHUB_TOKEN，跳过 PR 评论回写")
        return

    # 从 https://github.com/owner/repo.git 提取 owner/repo
    api_url = repo_url.rstrip("/").removesuffix(".git")
    if api_url.startswith("https://"):
        api_url = api_url.replace("https://github.com/", "https://api.github.com/repos/")
    comments_url = f"{api_url}/issues/{pr_number}/comments"

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    payload = {"body": comment_body}

    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(comments_url, headers=headers, json=payload)
            resp.raise_for_status()
            logger.info("GitHub PR 评论已发布: %s#%s", repo_url, pr_number)
    except Exception as exc:  # noqa: BLE001
        logger.exception("GitHub PR 评论回写失败: %s", exc)


def _post_gitlab_mr_comment(
    repo_url: str,
    mr_iid: int,
    comment_body: str,
    token: str | None,
) -> None:
    """在 GitLab MR 下发布评论。"""
    if not token:
        logger.warning("未配置 GITLAB_TOKEN，跳过 MR 评论回写")
        return

    # GitLab API 需要 URL 编码项目路径
    import urllib.parse

    api_base = repo_url.rstrip("/").removesuffix(".git")
    if api_base.startswith("https://"):
        # https://gitlab.com/owner/repo.git -> https://gitlab.com/api/v4/projects/owner%2Frepo
        api_base = api_base.replace("https://gitlab.com/", "https://gitlab.com/api/v4/projects/")
    encoded_project = urllib.parse.quote(api_base.split("projects/")[-1], safe="")
    comments_url = f"{api_base}/{encoded_project}/merge_requests/{mr_iid}/notes"

    headers = {"PRIVATE-TOKEN": token}
    payload = {"body": comment_body}

    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(comments_url, headers=headers, json=payload)
            resp.raise_for_status()
            logger.info("GitLab MR 评论已发布: %s!%s", repo_url, mr_iid)
    except Exception as exc:  # noqa: BLE001
        logger.exception("GitLab MR 评论回写失败: %s", exc)


def create_app(
    db_url: str | None = None,
    github_secret: str | None = None,
    github_token: str | None = None,
    gitlab_token: str | None = None,
    gitlab_api_token: str | None = None,
    default_branch: str = "main",
    default_tenant: str = "default",
) -> FastAPI:
    """创建 FastAPI 应用实例。

    参数
    ----------
    db_url : str | None
        存储后端连接串，None 时使用环境变量 ``HARNESS_DB`` 或默认 SQLite。
    github_secret : str | None
        GitHub Webhook 签名密钥，None 时使用环境变量 ``GITHUB_WEBHOOK_SECRET``。
    github_token : str | None
        GitHub API 令牌，用于在 PR 下发布评论回写，None 时使用环境变量 ``GITHUB_TOKEN``。
    gitlab_token : str | None
        GitLab Webhook 令牌，None 时使用环境变量 ``GITLAB_WEBHOOK_TOKEN``。
    gitlab_api_token : str | None
        GitLab API 令牌，用于在 MR 下发布评论回写，None 时使用环境变量 ``GITLAB_TOKEN``。
    default_branch : str
        默认基准分支。
    default_tenant : str
        默认租户名。
    """
    if db_url is None:
        db_url = os.environ.get("HARNESS_DB", str(Path("data") / "harness.db"))
    if github_secret is None:
        github_secret = os.environ.get("GITHUB_WEBHOOK_SECRET", "")
    if github_token is None:
        github_token = os.environ.get("GITHUB_TOKEN", "")
    if gitlab_token is None:
        gitlab_token = os.environ.get("GITLAB_WEBHOOK_TOKEN", "")
    if gitlab_api_token is None:
        gitlab_api_token = os.environ.get("GITLAB_TOKEN", "")

    app = FastAPI(title="PR-RSI Harness Webhook", version="0.1.0")

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/webhook/github")
    async def github_webhook(
        request: Request,
        background_tasks: BackgroundTasks,
        x_hub_signature_256: str | None = Header(default=None),
        x_github_event: str | None = Header(default=None),
    ) -> JSONResponse:
        payload_bytes = await request.body()

        if not verify_github_signature(payload_bytes, x_hub_signature_256, github_secret):
            raise HTTPException(status_code=401, detail="Invalid GitHub signature")

        try:
            payload = json.loads(payload_bytes)
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail="Invalid JSON payload")

        if x_github_event != "pull_request":
            return JSONResponse({"status": "ignored", "reason": "not a pull_request event"}, status_code=200)

        info = _extract_github_pr_info(payload)
        if info is None:
            return JSONResponse({"status": "ignored", "reason": "unsupported action or missing info"}, status_code=200)

        repo_url, pr_number, action = info
        event_id = f"github:{repo_url}:{pr_number}:{action}"
        if _idempotency.is_duplicate(event_id):
            return JSONResponse({"status": "ignored", "reason": "duplicate event"}, status_code=200)

        background_tasks.add_task(
            _run_pr_review,
            repo_url=repo_url,
            pr_number=pr_number,
            branch=default_branch,
            tenant=default_tenant,
            db_url=db_url,
            token=github_token,
        )
        return JSONResponse(
            {
                "status": "accepted",
                "repo": repo_url,
                "pr": pr_number,
                "action": action,
            },
            status_code=202,
        )

    @app.post("/webhook/gitlab")
    async def gitlab_webhook(
        request: Request,
        background_tasks: BackgroundTasks,
        x_gitlab_token: str | None = Header(default=None),
        x_gitlab_event: str | None = Header(default=None),
    ) -> JSONResponse:
        payload_bytes = await request.body()

        if not verify_gitlab_token(x_gitlab_token, gitlab_token):
            raise HTTPException(status_code=401, detail="Invalid GitLab token")

        try:
            payload = json.loads(payload_bytes)
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail="Invalid JSON payload")

        if x_gitlab_event != "Merge Request Hook":
            return JSONResponse({"status": "ignored", "reason": "not a merge_request event"}, status_code=200)

        info = _extract_gitlab_mr_info(payload)
        if info is None:
            return JSONResponse({"status": "ignored", "reason": "unsupported action or missing info"}, status_code=200)

        repo_url, mr_iid, action = info
        event_id = f"gitlab:{repo_url}:{mr_iid}:{action}"
        if _idempotency.is_duplicate(event_id):
            return JSONResponse({"status": "ignored", "reason": "duplicate event"}, status_code=200)

        background_tasks.add_task(
            _run_pr_review,
            repo_url=repo_url,
            pr_number=mr_iid,
            branch=default_branch,
            tenant=default_tenant,
            db_url=db_url,
            token=gitlab_api_token,
        )
        return JSONResponse(
            {
                "status": "accepted",
                "repo": repo_url,
                "mr": mr_iid,
                "action": action,
            },
            status_code=202,
        )

    return app