from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Finding:
    rule: str
    severity: str
    evidence: str
    line: str


RULES: list[tuple[str, str, re.Pattern[str]]] = [
    ("sql_injection", "high", re.compile(r"(SELECT|INSERT|UPDATE|DELETE).{0,40}\+|f[\"'].*(SELECT|INSERT)", re.I)),
    ("hardcoded_secret", "high", re.compile(r"(api[_-]?key|secret|password|token)\s*=\s*[\"'][^\"']{6,}", re.I)),
    ("command_injection", "high", re.compile(r"(os\.system|subprocess\.(call|run|Popen)).*\+|shell\s*=\s*True", re.I)),
    ("path_traversal", "medium", re.compile(r"open\([^)]*\+|os\.path\.join\([^)]*request", re.I)),
    ("xss", "medium", re.compile(r"(innerHTML|dangerouslySetInnerHTML|markupsafe\.Markup)\s*=", re.I)),
    ("weak_crypto", "medium", re.compile(r"md5\(|hashlib\.md5|DES\.new", re.I)),
    ("ssrf", "high", re.compile(r"requests\.(get|post)\([^)]*request\.(args|json|GET)", re.I)),
    ("insecure_deserialization", "high", re.compile(r"pickle\.loads|yaml\.load\(", re.I)),
]


def scan_diff(diff: str) -> list[Finding]:
    findings: list[Finding] = []
    seen: set[tuple[str, str]] = set()
    for line in diff.splitlines():
        if not line.startswith("+") or line.startswith("+++"):
            continue
        body = line[1:]
        for rule, severity, pattern in RULES:
            if pattern.search(body):
                key = (rule, body.strip())
                if key in seen:
                    continue
                seen.add(key)
                findings.append(Finding(rule=rule, severity=severity, evidence=body.strip(), line=line))
    return findings
