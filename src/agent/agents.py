from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from src.agent.llm import EchoLLM, LLMClient
from src.agent.scanner import Finding, scan_diff

DEFAULT_SYSTEM_PROMPT = "You identify code risks in a PR diff."


@dataclass
class VerifiedFinding:
    rule: str
    severity: str
    evidence: str
    verified: bool
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CriticFeedback:
    """Structured critic opinion produced by the result validator."""

    valid: bool
    opinions: list[str]


class RiskIdentifier:
    def __init__(self, llm: LLMClient | None = None, system_prompt: str | None = None) -> None:
        self.llm = llm or EchoLLM()
        self.system_prompt = system_prompt or DEFAULT_SYSTEM_PROMPT

    def run(
        self,
        diff: str,
        context: str | None = None,
        system_prompt: str | None = None,
    ) -> list[Finding]:
        findings = scan_diff(diff)
        _ = self.llm.chat(
            [
                {"role": "system", "content": system_prompt or self.system_prompt},
                {"role": "user", "content": (context or diff)[:4000]},
            ]
        )
        return findings


class EvidenceVerifier:
    def run(self, diff: str, findings: list[Finding]) -> list[VerifiedFinding]:
        verified: list[VerifiedFinding] = []
        for finding in findings:
            present = finding.evidence in diff
            verified.append(
                VerifiedFinding(
                    rule=finding.rule,
                    severity=finding.severity,
                    evidence=finding.evidence,
                    verified=present,
                    reason="evidence present in diff" if present else "evidence missing",
                )
            )
        return [item for item in verified if item.verified]


class SecureFixer:
    PATCHES = {
        "sql_injection": "use parameterized query instead of string concatenation",
        "hardcoded_secret": "load secret from environment or secret manager",
        "command_injection": "avoid shell=True and untrusted concatenation",
        "path_traversal": "resolve path against a trusted root and reject ..",
        "xss": "escape user-controlled HTML before rendering",
        "weak_crypto": "use SHA-256 or stronger algorithms",
        "ssrf": "allow-list outbound URLs",
        "insecure_deserialization": "avoid pickle.loads on untrusted data",
    }

    def run(self, findings: list[VerifiedFinding]) -> str:
        if not findings:
            return ""
        lines = ["# suggested secure fix"]
        for item in findings:
            advice = self.PATCHES.get(item.rule, "review and harden this change")
            lines.append(f"# fix {item.rule}: {advice}")
            lines.append(f"# evidence: {item.evidence}")
        return "\n".join(lines) + "\n"


class ResultValidator:
    def run(self, findings: list[VerifiedFinding], patch: str) -> CriticFeedback:
        if not findings:
            return CriticFeedback(valid=True, opinions=[])
        valid = bool(patch) and all(item.rule in patch for item in findings)
        opinions: list[str] = []
        if not valid:
            if not patch:
                opinions.append("patch is empty while risk findings exist")
            missing = [item.rule for item in findings if item.rule not in patch]
            if missing:
                opinions.append("patch missing fixes for: " + ", ".join(missing))
        return CriticFeedback(valid=valid, opinions=opinions)
