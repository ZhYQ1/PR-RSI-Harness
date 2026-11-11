"""从本地或远程 Git 仓库提取指定 PR 的 diff。

支持 GitHub / GitLab 的拉取请求引用，以及本地 Git 仓库路径。
所有 Git 操作通过 subprocess 调用系统 `git` 命令完成，无需额外 Python 依赖。
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path
from typing import Optional


def _run_git(args: list[str], cwd: Path | str) -> str:
    """运行 git 命令并返回标准输出；失败时抛出 CalledProcessError。"""
    result = subprocess.run(
        args,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    return result.stdout


def _authenticated_url(repo_url: str, token: Optional[str]) -> str:
    """为 HTTPS  URL 注入访问令牌（GitHub 使用 x-access-token，GitLab 使用 oauth2）。"""
    if not token or not repo_url.startswith("https://"):
        return repo_url
    # https://github.com/owner/repo.git -> https://x-access-token:<token>@github.com/owner/repo.git
    return repo_url.replace("https://", f"https://x-access-token:{token}@", 1)


def extract_pr_diff(
    repo_url: str,
    pr_number: int,
    branch: str = "main",
    work_dir: Optional[str] = None,
    token: Optional[str] = None,
    shallow: bool = True,
) -> str:
    """从 Git 仓库提取指定 PR 的 unified diff。

    参数
    ----------
    repo_url : str
        仓库地址，支持：
        - 远程 URL：`https://github.com/owner/repo.git`
        - 本地路径：`C:/Projects/demo_repo` 或 `/home/user/demo_repo`
    pr_number : int
        PR / 合并请求编号。
    branch : str, default "main"
        基准分支，用于计算 diff 的参照分支。
    work_dir : str | None, default None
        工作目录，None 时自动创建临时目录。
    token : str | None, default None
        访问令牌，用于私有仓库 HTTPS 认证。
    shallow : bool, default True
        是否浅克隆远程仓库（仅克隆最近提交，加快速度）。

    返回
    -------
    str
        标准 unified diff 文本，可直接传给 `pr-harness submit --diff`。
    """
    if work_dir is None:
        work_dir = tempfile.mkdtemp(prefix="pr-harness-git-")

    repo_path = Path(work_dir)
    repo_path.mkdir(parents=True, exist_ok=True)

    is_local = Path(repo_url).exists() and (Path(repo_url) / ".git").exists()

    if is_local:
        # 本地仓库：直接在目标目录操作
        repo_path = Path(repo_url)
        fetch_url = ""
    else:
        # 远程仓库：克隆到 work_dir
        fetch_url = _authenticated_url(repo_url, token)
        git_dir = repo_path / ".git"
        if not git_dir.exists():
            clone_args = ["git", "clone"]
            if shallow:
                clone_args += ["--depth", "1"]
            clone_args += [fetch_url, "."]
            _run_git(clone_args, cwd=repo_path)

    # 尝试获取 PR 引用：GitHub 使用 pull/{n}/head，GitLab 使用 merge-requests/{n}/head
    refs_to_try = [
        f"pull/{pr_number}/head",
        f"merge-requests/{pr_number}/head",
    ]

    pr_ref = None  # 最终确定的 PR 提交引用
    fetched = False

    if is_local:
        # 本地仓库：引用已是本地存在，无需 fetch，直接查找
        for ref_pattern in (f"refs/merge-requests/{pr_number}/head",
                            f"refs/pull/{pr_number}/head"):
            try:
                # rev-parse --verify --quiet 若引用存在则返回 0，否则返回非零
                _run_git(["git", "rev-parse", "--verify", "--quiet", ref_pattern], cwd=repo_path)
                pr_ref = ref_pattern
                fetched = True
                break
            except subprocess.CalledProcessError:
                continue
        if not fetched:
            raise RuntimeError(
                f"本地仓库 {repo_url} 中未找到 PR #{pr_number} 的引用，"
                "请确保已创建对应的 merge-request/pull ref"
            )
    else:
        # 远程仓库：从 origin 获取 PR 引用
        for ref in refs_to_try:
            try:
                _run_git(["git", "fetch", "origin", f"{ref}:pr-{pr_number}"], cwd=repo_path)
                pr_ref = f"pr-{pr_number}"
                fetched = True
                break
            except subprocess.CalledProcessError:
                continue

        if not fetched:
            raise RuntimeError(
                f"无法从远程仓库 {repo_url} 获取 PR #{pr_number} 的引用，"
                "请确认仓库地址正确且拥有读取权限"
            )

    # 切换到 PR 提交（本地引用直接检出，远程已创建 pr-{n} 分支）
    checkout_target = pr_ref if is_local else f"pr-{pr_number}"
    try:
        _run_git(["git", "checkout", checkout_target], cwd=repo_path)
    except subprocess.CalledProcessError:
        _run_git(["git", "checkout", "FETCH_HEAD"], cwd=repo_path)

    # 生成与基准分支的 diff
    try:
        diff = _run_git(["git", "diff", branch, "HEAD"], cwd=repo_path)
    except subprocess.CalledProcessError:
        # 回退：与 FETCH_HEAD 对比
        diff = _run_git(["git", "diff", branch, "FETCH_HEAD"], cwd=repo_path)

    return diff


def extract_local_diff(
    repo_path: str | Path,
    base_commit: str,
    target_commit: str = "HEAD",
) -> str:
    """提取本地仓库中两个提交之间的 diff（不涉及 PR 编号，供 Webhook/内部调用）。"""
    return _run_git(["git", "diff", base_commit, target_commit], cwd=Path(repo_path))