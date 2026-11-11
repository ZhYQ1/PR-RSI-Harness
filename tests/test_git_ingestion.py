"""Git 集成模块测试：验证从本地/远程仓库提取 PR diff。"""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

import pytest

from src.git.ingestion import extract_pr_diff, extract_local_diff


def _init_git_repo(path: Path, user_email: str = "test@example.com", user_name: str = "Test") -> None:
    """在指定目录初始化一个 Git 仓库，默认分支为 main，并提交一个初始文件。"""
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init"], cwd=str(path), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", user_email], cwd=str(path), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", user_name], cwd=str(path), check=True, capture_output=True)

    # 显式将默认分支命名为 main（避免不同 Git 版本默认 master/main 的差异）
    subprocess.run(["git", "symbolic-ref", "HEAD", "refs/heads/main"], cwd=str(path), check=True, capture_output=True)

    (path / "app.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    subprocess.run(["git", "add", "app.py"], cwd=str(path), check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=str(path), check=True, capture_output=True)


def _create_pr_ref(repo_path: Path, pr_number: int, ref_type: str = "merge-requests") -> None:
    """为本地仓库创建模拟的 PR 引用（refs/merge-requests/{n}/head 或 refs/pull/{n}/head）。"""
    # 获取当前 HEAD 作为 PR 分支的目标
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=str(repo_path), capture_output=True, text=True, check=True
    ).stdout.strip()

    ref = f"refs/{ref_type}/{pr_number}/head"
    subprocess.run(
        ["git", "update-ref", ref, head],
        cwd=str(repo_path),
        check=True,
        capture_output=True,
    )


def test_extract_local_diff_returns_unified_diff(tmp_path: Path) -> None:
    """extract_local_diff 应该返回两个提交之间的标准 unified diff。"""
    _init_git_repo(tmp_path)

    # 修改文件，加入一行风险代码
    app_py = tmp_path / "app.py"
    app_py.write_text(
        'def add(a, b):\n    return a + b\n\nquery = "SELECT * FROM users WHERE id=" + user_id\n',
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "app.py"], cwd=str(tmp_path), check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "add risky query"], cwd=str(tmp_path), check=True, capture_output=True)

    diff = extract_local_diff(str(tmp_path), "HEAD~1", "HEAD")
    assert "diff --git" in diff
    assert "+query = \"SELECT * FROM users WHERE id=\" + user_id" in diff


def test_extract_pr_diff_from_local_repo(tmp_path: Path) -> None:
    """extract_pr_diff 应该能从本地仓库的 PR 引用提取 diff。"""
    _init_git_repo(tmp_path)

    # 创建一个分支并提交风险代码，模拟 PR 分支
    subprocess.run(["git", "checkout", "-b", "feature/risky"], cwd=str(tmp_path), check=True, capture_output=True)
    app_py = tmp_path / "app.py"
    app_py.write_text(
        'def add(a, b):\n    return a + b\n\nAPI_KEY = "sk-live-1234567890abcdef"\n',
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "app.py"], cwd=str(tmp_path), check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "add hardcoded secret"], cwd=str(tmp_path), check=True, capture_output=True)

    # 切回 main，为 PR 引用做准备
    subprocess.run(["git", "checkout", "main"], cwd=str(tmp_path), check=True, capture_output=True)

    # 创建模拟 PR 引用指向 feature/risky 的提交
    pr_commit = subprocess.run(
        ["git", "rev-parse", "feature/risky"], cwd=str(tmp_path), capture_output=True, text=True, check=True
    ).stdout.strip()
    subprocess.run(
        ["git", "update-ref", "refs/merge-requests/1/head", pr_commit],
        cwd=str(tmp_path),
        check=True,
        capture_output=True,
    )

    diff = extract_pr_diff(str(tmp_path), pr_number=1, branch="main", shallow=False)
    assert "diff --git" in diff
    assert '+API_KEY = "sk-live-1234567890abcdef"' in diff


def test_extract_pr_diff_raises_on_missing_pr_ref(tmp_path: Path) -> None:
    """本地仓库没有对应 PR 引用时应抛出 RuntimeError。"""
    _init_git_repo(tmp_path)
    with pytest.raises(RuntimeError, match="未找到 PR #999 的引用"):
        extract_pr_diff(str(tmp_path), pr_number=999, branch="main", shallow=False)


def test_extract_pr_diff_raises_on_nonexistent_path(tmp_path: Path) -> None:
    """不存在的远程 URL / 路径应由 git 命令失败导致异常。"""
    nonexistent = tmp_path / "does_not_exist"
    with pytest.raises(subprocess.CalledProcessError):
        extract_pr_diff(str(nonexistent), pr_number=1, branch="main")


def test_extract_pr_diff_handles_clean_pr(tmp_path: Path) -> None:
    """无风险代码的 PR 也能正常提取 diff（只是没有 + 风险行）。"""
    _init_git_repo(tmp_path)

    subprocess.run(["git", "checkout", "-b", "feature/clean"], cwd=str(tmp_path), check=True, capture_output=True)
    app_py = tmp_path / "app.py"
    app_py.write_text("def add(a, b):\n    return a + b\n\ndef sub(a, b):\n    return a - b\n", encoding="utf-8")
    subprocess.run(["git", "add", "app.py"], cwd=str(tmp_path), check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "add clean function"], cwd=str(tmp_path), check=True, capture_output=True)

    subprocess.run(["git", "checkout", "main"], cwd=str(tmp_path), check=True, capture_output=True)
    clean_commit = subprocess.run(
        ["git", "rev-parse", "feature/clean"], cwd=str(tmp_path), capture_output=True, text=True, check=True
    ).stdout.strip()
    subprocess.run(
        ["git", "update-ref", "refs/merge-requests/2/head", clean_commit],
        cwd=str(tmp_path),
        check=True,
        capture_output=True,
    )

    diff = extract_pr_diff(str(tmp_path), pr_number=2, branch="main", shallow=False)
    assert "diff --git" in diff
    assert "+def sub(a, b):" in diff
    assert "API_KEY" not in diff
    assert "sql_injection" not in diff.lower()