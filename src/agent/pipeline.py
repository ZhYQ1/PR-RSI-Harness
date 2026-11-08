from __future__ import annotations

from typing import Any, Callable

from src.agent.agents import EvidenceVerifier, ResultValidator, RiskIdentifier, SecureFixer
from src.agent.llm import EchoLLM, LLMClient
from src.feedback.feedback import PRReviewFeedback
from src.harness.runtime import PipelineResult
from src.memory.compress import compress_diff
from src.memory.context import ContextAssembler
from src.memory.store import MemoryManager
from src.storage.models import PRTask


class ReviewPipeline:
    def __init__(
        self,
        memory: MemoryManager | None = None,
        llm: LLMClient | None = None,
        token_budget: int = 2000,
        prompt_provider: Callable[[], str] | None = None,
    ) -> None:
        self.memory = memory
        self.llm = llm or EchoLLM()
        self.token_budget = token_budget
        self.prompt_provider = prompt_provider
        self.assembler = ContextAssembler(memory)
        self.identifier = RiskIdentifier(self.llm)
        self.verifier = EvidenceVerifier()
        self.fixer = SecureFixer()
        self.validator = ResultValidator()

    def run(
        self,
        task: PRTask,
        checkpoint: dict[str, Any] | None,
        review_feedback: PRReviewFeedback | None = None,
    ) -> PipelineResult:
        compact, meta = compress_diff(task.diff, self.token_budget)
        critic_notes = self._load_critic_notes(task)
        context, context_meta = self.assembler.assemble(
            task.diff,
            critic_feedback=critic_notes,
            token_budget=self.token_budget,
            tenant=task.tenant,
            repo=task.repo,
            diff_section=compact,
        )
        system_prompt = self.prompt_provider() if self.prompt_provider else None
        findings = self.identifier.run(compact, context=context, system_prompt=system_prompt)
        verified = self.verifier.run(compact, findings)
        patch = self.fixer.run(verified)
        critic = self.validator.run(verified, patch)
        self._remember(task, verified)
        self._store_critic_notes(task, critic)
        return PipelineResult(
            findings=[item.rule for item in verified],
            patch=patch,
            validated=critic.valid,
            checkpoint={"phase": "VALIDATING", **(checkpoint or {})},
            details={
                "findings": [item.as_dict() for item in verified],
                "compress": meta,
                "context": context_meta,
                "critic": critic.opinions,
            },
            review_feedback=review_feedback,
        )

    def _remember(self, task: PRTask, verified) -> None:
        if self.memory is None:
            return
        summary = ", ".join(item.rule for item in verified) or "clean"
        self.memory.put("working", task.tenant, task.repo, f"findings: {summary}", task.id)
        self.memory.put("scenario", task.tenant, task.repo, f"pr {task.pr_number}: {summary}", task.id)
        if verified:
            self.memory.put(
                "semantic",
                task.tenant,
                task.repo,
                f"pattern {verified[0].rule} seen in {task.repo}",
            )

    def _load_critic_notes(self, task: PRTask) -> list[str]:
        if self.memory is None:
            return []
        records = self.memory.search(task.tenant, task.repo, "critic")
        return [record.content.removeprefix("critic: ") for record in records]

    def _store_critic_notes(self, task: PRTask, critic) -> None:
        if self.memory is None or critic.valid:
            return
        for opinion in critic.opinions:
            self.memory.put("working", task.tenant, task.repo, f"critic: {opinion}", task.id)
