"""
skills-auditor — read-mostly drift detector for Claude Code skill scopes.

Public API (Phase 1 per spec §4):
    scan(roots=None, include_kinds=None) -> list[Finding]
    report(findings, dry_run=True)       -> ReportSummary
    audit()                              -> ReportSummary   # scan → report(dry_run=False)

All findings are persisted through `agent_memory` (Notion) — the auditor holds
no local storage of its own. Finding identity = (kind, sorted involved paths).
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from skills_auditor.models import (
    Finding,
    FindingKind,
    ReportSummary,
    SEVERITY_BY_KIND,
    SkillFile,
)
from skills_auditor.detectors import run_all
from skills_auditor.scanner import scan_disk
from skills_auditor.reporter import report

__version__ = "0.1.0"


def scan(
    roots: Iterable[Path] | None = None,
    include_kinds: list[str] | None = None,
) -> list[Finding]:
    """Walk skill scopes and return Finding objects. Pure function — no Notion writes."""
    inventory = scan_disk(roots)
    return run_all(inventory, include_kinds=include_kinds)


def audit() -> ReportSummary:
    """Convenience: scan() then report(dry_run=False). Entry point for cron/Action."""
    findings = scan()
    return report(findings, dry_run=False)


__all__ = [
    "Finding",
    "FindingKind",
    "ReportSummary",
    "SEVERITY_BY_KIND",
    "SkillFile",
    "scan",
    "report",
    "audit",
    "__version__",
]
