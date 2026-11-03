from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.agent.agents import SecureFixer
from src.memory.compress import compress_diff
from src.memory.store import MemoryManager


def _tokens(text: str) -> int:
    return max(1, len(text.split()))


@dataclass
class ContextItem:
    priority: int  # lower means more important
    label: str
    content: str


class ContextAssembler:
    """Assemble a token-budgeted review context from prioritized sections.

    Fill order: high-risk diff hunks > critic feedback > tool descriptions >
    historical observations from memory.
    """

    def __init__(self, memory: MemoryManager | None = None) -> None:
        self.memory = memory

    def assemble(
        self,
        diff: str,
        critic_feedback: list[str] | None = None,
        token_budget: int = 2000,
        tenant: str = "default",
        repo: str = "default",
        diff_section: str | None = None,
    ) -> tuple[str, dict[str, Any]]:
        items: list[ContextItem] = []

        if diff_section is None:
            diff_section, _ = compress_diff(diff, token_budget)
        items.append(ContextItem(priority=0, label="diff", content=diff_section))

        if critic_feedback:
            items.append(
                ContextItem(
                    priority=1,
                    label="critic",
                    content="\n".join(f"- {opinion}" for opinion in critic_feedback),
                )
            )

        tools = "\n".join(f"- {rule}: {advice}" for rule, advice in SecureFixer.PATCHES.items())
        items.append(ContextItem(priority=2, label="tools", content=tools))

        if self.memory is not None:
            history = self.memory.search(tenant, repo, repo)
            if history:
                lines = "\n".join(f"- {record.content}" for record in history[-20:])
                items.append(ContextItem(priority=3, label="history", content=lines))

        sections: list[str] = []
        used = 0
        truncated: list[str] = []
        for item in sorted(items, key=lambda entry: entry.priority):
            remaining = token_budget - used
            if remaining <= 0:
                truncated.append(item.label)
                continue
            content = item.content
            cost = _tokens(content)
            if cost > remaining:
                content = " ".join(content.split()[:remaining])
                cost = remaining
                truncated.append(item.label)
            if content.strip():
                sections.append(f"[{item.label}]\n{content}")
                used += cost
        return "\n\n".join(sections), {
            "tokens": used,
            "budget": token_budget,
            "truncated": truncated,
            "sections": [s.label for s in sorted(items, key=lambda entry: entry.priority)],
        }
