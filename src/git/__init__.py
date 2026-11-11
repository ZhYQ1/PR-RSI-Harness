"""Git 集成模块：从本地或远程 Git 仓库提取 PR diff。"""

from src.git.ingestion import extract_pr_diff

__all__ = ["extract_pr_diff"]