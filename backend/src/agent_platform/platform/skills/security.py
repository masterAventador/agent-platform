from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import PurePosixPath

from agent_platform.platform.skills.bundle import SkillBundle


class SkillFindingSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    BLOCKER = "blocker"


class SkillReviewStatus(StrEnum):
    APPROVED = "approved"
    BLOCKED = "blocked"


@dataclass(frozen=True, slots=True)
class SkillSecurityFinding:
    severity: SkillFindingSeverity
    category: str
    code: str
    message: str
    path: str | None = None

    def as_dict(self) -> dict[str, str | None]:
        return {
            "severity": self.severity.value,
            "category": self.category,
            "code": self.code,
            "message": self.message,
            "path": self.path,
        }

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> SkillSecurityFinding:
        path = value.get("path")
        return cls(
            severity=SkillFindingSeverity(str(value["severity"])),
            category=str(value["category"]),
            code=str(value["code"]),
            message=str(value["message"]),
            path=str(path) if path is not None else None,
        )


@dataclass(frozen=True, slots=True)
class SkillSecurityReport:
    status: SkillReviewStatus
    findings: list[SkillSecurityFinding]

    def as_findings_json(self) -> list[dict[str, str | None]]:
        return [finding.as_dict() for finding in self.findings]

    @classmethod
    def from_findings_json(cls, values: list[dict[str, object]]) -> SkillSecurityReport:
        findings = [SkillSecurityFinding.from_dict(value) for value in values]
        status = (
            SkillReviewStatus.BLOCKED
            if any(finding.severity is SkillFindingSeverity.BLOCKER for finding in findings)
            else SkillReviewStatus.APPROVED
        )
        return cls(status=status, findings=findings)


SCRIPT_EXTENSIONS = {".py", ".sh", ".bash", ".zsh", ".js", ".ts", ".mjs", ".ps1"}
DEPENDENCY_FILES = {
    "requirements.txt",
    "pyproject.toml",
    "package.json",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
}
SCRIPT_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\brm\s+-rf\s+/",
        r"\bos\.system\s*\(",
        r"\bsubprocess\.",
        r"\beval\s*\(",
        r"\bexec\s*\(",
        r"\bcurl\b.*\|\s*(?:sh|bash)",
        r"\bchmod\s+777\b",
    )
]
DEPENDENCY_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\bgit\+https?://",
        r"\bhttps?://",
        r"\bfile:",
        r"\bpath\s*=",
    )
]
DANGEROUS_CONTENT_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"ignore\s+(?:all\s+)?previous\s+instructions",
        r"exfiltrat(?:e|ion)",
        r"steal\s+(?:secrets|credentials|tokens)",
        r"BEGIN\s+(?:RSA\s+)?PRIVATE\s+KEY",
        r"AWS_SECRET_ACCESS_KEY",
    )
]


def audit_skill_bundle(bundle: SkillBundle) -> SkillSecurityReport:
    findings = [
        SkillSecurityFinding(
            severity=SkillFindingSeverity.INFO,
            category="archive",
            code="archive_scanned",
            message=f"ZIP 包结构已审核，共 {len(bundle.files)} 个文件",
        ),
        SkillSecurityFinding(
            severity=SkillFindingSeverity.INFO,
            category="path",
            code="paths_normalized",
            message="所有文件路径均已规范化，未发现绝对路径、回退路径或符号链接",
        ),
        SkillSecurityFinding(
            severity=SkillFindingSeverity.INFO,
            category="size",
            code="size_within_limit",
            message="压缩包、文件数量和单文件大小均在平台限制内",
        ),
    ]
    for path in bundle.files:
        content = bundle.read_bytes(path)
        findings.extend(_script_findings(path=path, content=content))
        findings.extend(_dependency_findings(path=path, content=content))
        findings.extend(_dangerous_content_findings(path=path, content=content))
    status = (
        SkillReviewStatus.BLOCKED
        if any(finding.severity is SkillFindingSeverity.BLOCKER for finding in findings)
        else SkillReviewStatus.APPROVED
    )
    return SkillSecurityReport(status=status, findings=findings)


def _script_findings(path: str, content: bytes) -> list[SkillSecurityFinding]:
    suffix = PurePosixPath(path).suffix.lower()
    if not path.startswith("scripts/") and suffix not in SCRIPT_EXTENSIONS:
        return []
    text = _safe_text(content)
    findings = [
        SkillSecurityFinding(
            severity=SkillFindingSeverity.WARNING,
            category="script",
            code="script_present",
            message="包含脚本文件，运行时必须进入沙箱并按最小权限执行",
            path=path,
        )
    ]
    if any(pattern.search(text) for pattern in SCRIPT_PATTERNS):
        findings.append(
            SkillSecurityFinding(
                severity=SkillFindingSeverity.BLOCKER,
                category="script",
                code="dangerous_script",
                message="脚本包含高危系统命令或动态执行行为，禁止发布",
                path=path,
            )
        )
    return findings


def _dependency_findings(path: str, content: bytes) -> list[SkillSecurityFinding]:
    name = PurePosixPath(path).name
    if name not in DEPENDENCY_FILES:
        return []
    text = _safe_text(content)
    severity = (
        SkillFindingSeverity.BLOCKER
        if any(pattern.search(text) for pattern in DEPENDENCY_PATTERNS)
        else SkillFindingSeverity.WARNING
    )
    return [
        SkillSecurityFinding(
            severity=severity,
            category="dependency",
            code="dependency_manifest_reviewed"
            if severity is SkillFindingSeverity.WARNING
            else "untrusted_dependency_source",
            message=(
                "依赖清单已识别，发布前需确认沙箱依赖来源"
                if severity is SkillFindingSeverity.WARNING
                else "依赖清单包含 URL、Git 或本地路径来源，禁止发布"
            ),
            path=path,
        )
    ]


def _dangerous_content_findings(path: str, content: bytes) -> list[SkillSecurityFinding]:
    if _looks_binary(content):
        return []
    text = _safe_text(content)
    if not any(pattern.search(text) for pattern in DANGEROUS_CONTENT_PATTERNS):
        return []
    return [
        SkillSecurityFinding(
            severity=SkillFindingSeverity.BLOCKER,
            category="dangerous_content",
            code="prompt_injection_or_secret",
            message="Skill 内容包含提示注入、外泄指令或疑似密钥，禁止发布",
            path=path,
        )
    ]


def _safe_text(content: bytes) -> str:
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError:
        return ""


def _looks_binary(content: bytes) -> bool:
    if not content:
        return False
    return b"\0" in content[:1024]


def findings_from_json(values: object) -> list[SkillSecurityFinding]:
    if not isinstance(values, list):
        return []
    normalized: list[SkillSecurityFinding] = []
    for value in values:
        if isinstance(value, dict):
            normalized.append(SkillSecurityFinding.from_dict(value))
    return normalized


def findings_to_json(findings: list[SkillSecurityFinding]) -> list[dict[str, str | None]]:
    return [finding.as_dict() for finding in findings]


def findings_json_dumps(findings: list[SkillSecurityFinding]) -> str:
    return json.dumps(findings_to_json(findings), ensure_ascii=False, sort_keys=True)
