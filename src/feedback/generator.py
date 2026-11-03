from __future__ import annotations

import os
from typing import Any

from src.agent.agents import DEFAULT_SYSTEM_PROMPT
from src.agent.llm import LLMClient, SparkLLM

GUIDELINES: dict[str, str] = {
    "sql_injection": "always require parameterized queries; flag any string concatenation or f-string interpolation into SQL",
    "hardcoded_secret": "never allow hardcoded credentials; require environment or secret-manager lookups",
    "command_injection": "reject shell command construction from untrusted input; forbid shell=True",
    "path_traversal": "resolve file paths against a trusted root and reject traversal segments",
    "xss": "escape user-controlled HTML before rendering",
    "weak_crypto": "require SHA-256 or stronger; flag md5/sha1/DES usage",
    "ssrf": "require an allow-list for outbound URLs built from request data",
    "insecure_deserialization": "forbid pickle/yaml.load on untrusted input; require safe loaders",
}

_TEMPLATE = """You are the PR risk identification agent. {base}
Additional mandatory checks (learned from failures in cluster "{cluster}"):
{checks}"""


class CandidateGenerator:
    """Generate Prompt/Skill candidates from failure clusters.

    Prefers the configured LLM; falls back to a deterministic rule template
    whenever no LLM is available or the call fails.
    """

    def __init__(self, llm: LLMClient | None = None) -> None:
        self.llm = llm

    def generate(self, cluster_label: str, samples: list[dict[str, Any]]) -> tuple[str, str]:
        """Return (kind, content) for the candidate version."""
        content = ""
        if self._llm_available():
            try:
                content = self._llm_generate(cluster_label, samples)
            except Exception:  # noqa: BLE001 — any LLM failure falls back to template
                content = ""
        if not content or not content.strip():
            content = self._template(cluster_label)
        return "prompt", content

    def _llm_available(self) -> bool:
        return self.llm is not None or bool(os.environ.get("SPARK_API_KEY"))

    def _get_llm(self) -> LLMClient:
        return self.llm or SparkLLM()

    def _llm_generate(self, cluster_label: str, samples: list[dict[str, Any]]) -> str:
        summary = "\n".join(
            f"- case_type={s.get('case_type')} label={s.get('failure_label')} "
            f"predicted={s.get('predicted')} expected={s.get('expected')}"
            for s in samples[:10]
        )
        prompt = (
            f'The review agent keeps failing with pattern "{cluster_label}". '
            f"Failure samples:\n{summary}\n"
            "Rewrite the agent's system prompt to prevent these failures. "
            "Reply with the new system prompt only."
        )
        return self._get_llm().chat(
            [
                {"role": "system", "content": "You improve agent prompts based on failure feedback."},
                {"role": "user", "content": prompt},
            ]
        )

    def _template(self, cluster_label: str) -> str:
        rule = cluster_label.split(":", 1)[1] if ":" in cluster_label else ""
        guideline = GUIDELINES.get(rule)
        checks = f"- {guideline}" if guideline else "- re-verify evidence against the diff before finalizing findings"
        return _TEMPLATE.format(base=DEFAULT_SYSTEM_PROMPT, cluster=cluster_label, checks=checks)
