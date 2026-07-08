"""
Data types for skills-auditor.

All types are intentionally small dataclasses. The scanner produces `SkillFile`
objects (inventory); detectors consume that inventory and emit `Finding` objects;
the reporter turns findings into Notion rows via `agent_memory`.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Literal

Scope = Literal["global", "project", "plugin"]

FindingKind = Literal[
    "duplication",
    "missing-defer-pattern",
    "stale-reference",
    "orphan",
    "naming-collision",
    "zip-divergence",
]

Severity = Literal["info", "warn", "drift-detected"]

SEVERITY_BY_KIND: dict[str, Severity] = {
    "duplication": "drift-detected",
    "missing-defer-pattern": "drift-detected",
    "stale-reference": "warn",
    "orphan": "info",
    "naming-collision": "warn",
    "zip-divergence": "info",
}


@dataclass
class SkillFile:
    """One skill entry on disk.

    `path` is the authoritative location — either a directory containing SKILL.md
    (global/plugin convention) or a standalone .md file (in-repo convention).
    `content_path` is the actual file whose body was parsed (SKILL.md or the .md).
    """

    name: str
    scope: Scope
    project: str | None  # e.g. 'job-search-agent-v2' (project scope) or plugin name
    path: Path  # directory for dir-style skills; file for standalone .md
    content_path: Path
    description: str
    body: str
    section_headers: list[str] = field(default_factory=list)

    @property
    def is_global(self) -> bool:
        return self.scope == "global"


@dataclass
class Finding:
    """One open issue the auditor detected. Goes to Notion as a page in Agent Memory."""

    kind: FindingKind
    files: list[Path]  # sorted, canonical paths — identity basis
    detected_on: date
    what: str  # what was detected (concrete, short)
    recommended_action: str
    why_it_matters: str
    severity: Severity = field(init=False)

    def __post_init__(self) -> None:
        self.severity = SEVERITY_BY_KIND[self.kind]
        self.files = sorted({Path(p) for p in self.files}, key=str)

    @property
    def identity(self) -> tuple[str, tuple[str, ...]]:
        """Stable identity: (kind, sorted file paths as strings)."""
        return (self.kind, tuple(str(p) for p in self.files))

    @property
    def slug(self) -> str:
        """Short, human-readable slug for the Notion Name column.

        Format: skills-audit-<kind>-<short-hash>-<primary-stem>
        The hash disambiguates while the stem stays greppable.
        """
        h = hashlib.sha1(repr(self.identity).encode()).hexdigest()[:6]
        primary = self.files[0].stem if self.files else "unknown"
        # strip common suffixes to keep it readable
        primary = primary.replace(".zip", "")
        return f"skills-audit-{self.kind}-{primary}-{h}"

    @property
    def description(self) -> str:
        """One-line Description (≤~180 chars)."""
        return self.what[:180]

    def render_content(self) -> str:
        """Markdown body for the Content column, matching spec §3."""
        files_block = "\n".join(
            f"- `{p}`" for p in self.files
        )
        return (
            f"**Kind:** {self.kind}\n"
            f"**Detected:** {self.detected_on.isoformat()}\n"
            f"**Severity:** {self.severity}\n\n"
            f"**Files involved:**\n{files_block}\n\n"
            f"**What I detected:**\n{self.what}\n\n"
            f"**Recommended action:**\n{self.recommended_action}\n\n"
            f"**Why this matters:**\n{self.why_it_matters}\n"
        )


@dataclass
class ReportSummary:
    """Counts returned from report() — what happened against Notion."""

    created: int = 0
    verified: int = 0
    superseded: int = 0
    skipped: int = 0
    dry_run: bool = True

    def total(self) -> int:
        return self.created + self.verified + self.superseded + self.skipped
