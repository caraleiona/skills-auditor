"""
Per-kind detectors. Each detector takes the full inventory plus any auxiliary
state (disk roots, reference-scraping cache) and returns a list of Findings.

All detectors are pure functions over the SkillFile list — no Notion calls here.
"""

from __future__ import annotations

import logging
import os
import re
from datetime import date
from pathlib import Path

from skills_auditor.models import Finding, SkillFile
from skills_auditor.scanner import (
    DEFAULT_GLOBAL_ROOT,
    DEFAULT_PROJECTS_ROOT,
    EXTRA_PROJECT_ENV,
)

logger = logging.getLogger("skills_auditor")

# Tunable: Jaccard threshold for flagging duplication. Spec §4 sets 0.6 as v1.
JACCARD_THRESHOLD = 0.6

# ~80-char cap on any raw skill text in a finding body (spec §7 governance rule)
SNIPPET_CAP = 80

# Stopwords for the Jaccard tokenizer. Short, English-only, pragmatic.
_STOPWORDS = frozenset(
    """
    a an and are as at be but by for from has have if in into is it its of on or
    so that the their them then there these they this to was were will with you
    your use used using uses when where which who whom how why what skill
    """.split()
)

_WORD_RE = re.compile(r"[a-z0-9][a-z0-9'-]+")

# Patterns that indicate the skill correctly defers to an authoritative source.
# Any one match → the skill acknowledges its lineage; missing-defer-pattern does NOT fire.
_DEFER_PATTERNS = [
    re.compile(r"authoritative", re.IGNORECASE),
    re.compile(r"~/\.claude/skills/", re.IGNORECASE),
    re.compile(r"\bprecedence\b", re.IGNORECASE),
    re.compile(r"\bdo not duplicate\b", re.IGNORECASE),
    re.compile(r"if (?:they )?(?:ever )?disagree", re.IGNORECASE),
    re.compile(r"(?:wins|owns) (?:if|the)", re.IGNORECASE),
]


def _tokens(text: str) -> set[str]:
    words = _WORD_RE.findall(text.lower())
    return {w for w in words if w not in _STOPWORDS and len(w) > 2}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _defer_pattern_present(body: str) -> bool:
    return any(p.search(body) for p in _DEFER_PATTERNS)


# ---------- Individual detectors ----------


def detect_naming_collisions(inventory: list[SkillFile]) -> list[Finding]:
    """Same skill name appearing in ≥2 scopes."""
    by_name: dict[str, list[SkillFile]] = {}
    for s in inventory:
        by_name.setdefault(s.name, []).append(s)

    out: list[Finding] = []
    for name, group in by_name.items():
        if len(group) < 2:
            continue
        # distinct scopes/paths only; duplicates in same scope would be a filesystem bug
        scopes = {s.scope for s in group}
        if len(scopes) < 2:
            continue
        files = [s.content_path for s in group]
        scope_list = ", ".join(sorted({f"{s.scope}" + (f":{s.project}" if s.project else "") for s in group}))
        out.append(
            Finding(
                kind="naming-collision",
                files=files,
                detected_on=date.today(),
                what=(
                    f"Skill name `{name}` exists in multiple scopes ({scope_list}). "
                    "Risk: one shadows the other depending on invocation context."
                ),
                recommended_action=(
                    "Decide which scope owns the skill. Rename the subordinate copy "
                    "(e.g. add a project-specific suffix) so the two names never "
                    "collide during resolution."
                ),
                why_it_matters=(
                    "Global skills own durable craft; in-repo skills own project procedure. "
                    "A name collision lets either one accidentally take precedence."
                ),
            )
        )
    return out


def _overlap_signal(a: SkillFile, b: SkillFile) -> float:
    """Jaccard on (description + headers) of two skills, lowercased & tokenized."""
    sig_a = _tokens(a.description + "\n" + " ".join(a.section_headers))
    sig_b = _tokens(b.description + "\n" + " ".join(b.section_headers))
    return _jaccard(sig_a, sig_b)


def detect_duplication_and_missing_defer(
    inventory: list[SkillFile],
) -> list[Finding]:
    """In-repo skills that overlap with a global skill's domain.

    If Jaccard(desc+headers) ≥ THRESHOLD with any global skill:
      - if the in-repo body contains a defer pattern → emit `duplication` (info-grade)
      - otherwise → emit `missing-defer-pattern` (the more actionable flavor)

    We never emit BOTH for the same pair — missing-defer-pattern supersedes
    duplication when there's no deference.
    """
    globals_ = [s for s in inventory if s.scope == "global"]
    in_repo = [s for s in inventory if s.scope == "project"]

    out: list[Finding] = []
    for repo_skill in in_repo:
        for g in globals_:
            # skip pairs where names match — naming-collision covers that
            if repo_skill.name == g.name:
                continue
            score = _overlap_signal(repo_skill, g)
            if score < JACCARD_THRESHOLD:
                continue
            has_defer = _defer_pattern_present(repo_skill.body)
            files = [repo_skill.content_path, g.content_path]
            if has_defer:
                out.append(
                    Finding(
                        kind="duplication",
                        files=files,
                        detected_on=date.today(),
                        what=(
                            f"In-repo `{repo_skill.name}` "
                            f"(in {repo_skill.project or 'unknown'}) overlaps with global "
                            f"`{g.name}` at Jaccard {score:.2f} on description + headers. "
                            "Defer pattern is present — relationship is declared; check "
                            "for content drift."
                        ),
                        recommended_action=(
                            "Read both skills side-by-side. If the in-repo version "
                            "still adds only project-procedure on top of the global "
                            "craft, leave it. If overlap grew, surgically trim the "
                            "overlap back to a pointer."
                        ),
                        why_it_matters=(
                            "Global owns durable craft; in-repo owns project procedure. "
                            "Overlap without active maintenance is how drift starts."
                        ),
                    )
                )
            else:
                out.append(
                    Finding(
                        kind="missing-defer-pattern",
                        files=files,
                        detected_on=date.today(),
                        what=(
                            f"In-repo `{repo_skill.name}` "
                            f"(in {repo_skill.project or 'unknown'}) overlaps with global "
                            f"`{g.name}` at Jaccard {score:.2f} but contains no "
                            "'Authoritative source' / precedence / ~/.claude/skills/ reference."
                        ),
                        recommended_action=(
                            "Restructure: add an 'Authoritative sources' section near "
                            "the top that names the global skill and declares precedence. "
                            "Then trim whatever the in-repo version restates rather than "
                            "applies. Do not auto-execute — this is a voice/structure call."
                        ),
                        why_it_matters=(
                            "Without a declared authoritative source, future edits will "
                            "silently diverge. The cover-letter-drafter incident "
                            "(2026-04-18) is the canonical example of this failure mode."
                        ),
                    )
                )
    return out


_REFERENCE_FILES_HINT = [
    Path.home() / "ClaudeProjects" / "CLAUDE.md",
    Path.home() / ".claude" / "projects" / "-Users-carawilson-ClaudeProjects" / "memory" / "MEMORY.md",
]


_IGNORE_PARTS = {".venv", "node_modules", "__pycache__", ".git", ".github"}


def _walk_project_refs(root: Path) -> list[Path]:
    """Find CLAUDE.md + README.md under a single project root, skipping noise dirs."""
    out: list[Path] = []
    if not root.exists():
        return out
    for pattern in ("CLAUDE.md", "README.md"):
        for hit in root.rglob(pattern):
            if any(part in _IGNORE_PARTS for part in hit.parts):
                continue
            out.append(hit)
    return out


def _collect_project_reference_files() -> list[Path]:
    """CLAUDE.md + README.md under ~/ClaudeProjects + any SKILLS_AUDITOR_EXTRA_PROJECTS,
    plus the user's MEMORY.md index + its linked *.md files.

    The extra-projects branch matters in CI: on a GH runner ~/ClaudeProjects is
    empty, so without this, the orphan detector emits false positives for any
    in-repo skill whose only reference lives in the checked-out repo's CLAUDE.md.
    """
    out: list[Path] = [p for p in _REFERENCE_FILES_HINT if p.is_file()]
    out += _walk_project_refs(DEFAULT_PROJECTS_ROOT)

    extra = os.environ.get(EXTRA_PROJECT_ENV, "").strip()
    if extra:
        for raw in extra.split(":"):
            p = Path(raw).expanduser().resolve()
            if p.is_dir():
                out += _walk_project_refs(p)

    mem_dir = Path.home() / ".claude" / "projects" / "-Users-carawilson-ClaudeProjects" / "memory"
    if mem_dir.is_dir():
        for md in mem_dir.glob("*.md"):
            out.append(md)
    return out


# Heuristic patterns that suggest a reference to a skill by name
# e.g. `~/.claude/skills/<name>/`, `/.claude/skills/<name>.md`, or backtick-wrapped `<name>` skill mention
_SKILL_MENTION_RES = [
    re.compile(r"\.claude/skills/([a-zA-Z0-9_-]+?)(?:/|\.md|/SKILL\.md)"),
    re.compile(r"`([a-zA-Z0-9_-]{3,40})`\s+skill"),
    re.compile(r"skill\s+`([a-zA-Z0-9_-]{3,40})`"),
]


def detect_stale_references(inventory: list[SkillFile]) -> list[Finding]:
    """A skill name appears in CLAUDE.md / memory files but no skill at that path exists.

    Skipped when the global skills root isn't on this host: without visibility
    into `~/.claude/skills/`, any name referenced via a path-style pointer
    looks "missing" even when it exists on the author's machine. This is the
    CI case — false-positive avoidance trumps missing a real stale reference
    that the next local/disk run will catch anyway.
    """
    if not DEFAULT_GLOBAL_ROOT.exists():
        logger.info(
            "detect_stale_references: global skills root %s not readable — skipping",
            DEFAULT_GLOBAL_ROOT,
        )
        return []
    existing_names = {s.name for s in inventory}
    ref_files = _collect_project_reference_files()

    mentioned: dict[str, list[Path]] = {}
    for ref in ref_files:
        try:
            text = ref.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for rx in _SKILL_MENTION_RES:
            for m in rx.finditer(text):
                candidate = m.group(1)
                if candidate in {"skills", "SKILL", "skill"}:
                    continue
                mentioned.setdefault(candidate, []).append(ref)

    out: list[Finding] = []
    for name, where in mentioned.items():
        if name in existing_names:
            continue
        unique_wheres = sorted({w for w in where})
        out.append(
            Finding(
                kind="stale-reference",
                files=unique_wheres,
                detected_on=date.today(),
                what=(
                    f"Name `{name}` is referenced as a skill in "
                    f"{len(unique_wheres)} file(s) but no skill by that name exists "
                    "on disk under global, project, or plugin scopes."
                ),
                recommended_action=(
                    "Either restore the skill (if it was lost) or scrub the reference "
                    "(if the skill was renamed/retired). Leaving both in place trains "
                    "future sessions to expect something that isn't there."
                ),
                why_it_matters=(
                    "Stale pointers in durable docs get followed before they get "
                    "questioned. Better to remove than to leave load-bearing rot."
                ),
            )
        )
    return out


def detect_orphans(inventory: list[SkillFile]) -> list[Finding]:
    """In-repo skill files that nothing references (CLAUDE.md, MEMORY.md, README)."""
    in_repo = [s for s in inventory if s.scope == "project"]
    if not in_repo:
        return []

    # READMEs are already included by _collect_project_reference_files via
    # _walk_project_refs; no need to duplicate the walk here.
    ref_files = _collect_project_reference_files()

    ref_blob = ""
    for ref in ref_files:
        try:
            ref_blob += "\n" + ref.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue

    out: list[Finding] = []
    for s in in_repo:
        # search for name as-is or as a path fragment
        if s.name in ref_blob:
            continue
        out.append(
            Finding(
                kind="orphan",
                files=[s.content_path],
                detected_on=date.today(),
                what=(
                    f"In-repo skill `{s.name}` "
                    f"(in {s.project or 'unknown'}) is not referenced by any CLAUDE.md, "
                    "MEMORY.md, or project README."
                ),
                recommended_action=(
                    "Either add a pointer in the project's CLAUDE.md (if this skill is "
                    "load-bearing and future sessions should know about it) or retire it "
                    "if it's been superseded."
                ),
                why_it_matters=(
                    "A skill that nothing references is a skill that won't get invoked. "
                    "Either it earns its keep via a pointer or it's silently dead weight."
                ),
            )
        )
    return out


def detect_zip_divergence(inventory: list[SkillFile], root: Path = DEFAULT_GLOBAL_ROOT) -> list[Finding]:
    """For each `<root>/<name>.zip`, compare content to `<root>/<name>/SKILL.md`.

    'Divergence' means different bytes in the SKILL.md at the two locations.
    We don't unzip to a temp dir — we inspect the zip's SKILL.md in-place.
    """
    if not root.exists():
        return []
    import zipfile

    out: list[Finding] = []
    for zip_path in sorted(root.glob("*.zip")):
        name = zip_path.stem
        dir_skill = root / name / "SKILL.md"
        if not dir_skill.is_file():
            continue
        try:
            with zipfile.ZipFile(zip_path) as zf:
                # Zip may contain either SKILL.md at root or under <name>/SKILL.md
                members = zf.namelist()
                candidate = None
                for m in members:
                    if m.endswith("SKILL.md"):
                        candidate = m
                        break
                if candidate is None:
                    continue
                zip_body = zf.read(candidate).decode("utf-8", errors="replace")
        except (zipfile.BadZipFile, OSError) as e:
            logger.warning("zip-divergence: could not read %s: %s", zip_path, e)
            continue

        disk_body = dir_skill.read_text(encoding="utf-8", errors="replace")
        if zip_body.strip() == disk_body.strip():
            continue

        # crude size-delta signal, no raw content in the finding body
        delta = len(disk_body) - len(zip_body)
        out.append(
            Finding(
                kind="zip-divergence",
                files=[zip_path, dir_skill],
                detected_on=date.today(),
                what=(
                    f"`{zip_path.name}` exists alongside `{name}/SKILL.md` but the "
                    f"SKILL.md bodies differ (disk vs zip Δ = {delta:+d} chars). "
                    "Either the zip is a stale export or the directory has diverged "
                    "from what was last shared."
                ),
                recommended_action=(
                    "Decide which is authoritative. If the zip is a snapshot export "
                    "for claude.ai, re-export from the disk version. If the zip was "
                    "the last-shared source and disk drifted locally, reconcile by "
                    "hand — both are voice-sensitive files."
                ),
                why_it_matters=(
                    "CLI edits don't propagate to the claude.ai skill twin — the zip "
                    "is the manual sync bridge. Divergence means one surface is stale."
                ),
            )
        )
    return out


# ---------- Dispatcher ----------

_ALL_DETECTORS = {
    "duplication": detect_duplication_and_missing_defer,
    "missing-defer-pattern": detect_duplication_and_missing_defer,  # same fn, emits both
    "stale-reference": detect_stale_references,
    "orphan": detect_orphans,
    "naming-collision": detect_naming_collisions,
    "zip-divergence": detect_zip_divergence,
}


def run_all(inventory: list[SkillFile], include_kinds: list[str] | None = None) -> list[Finding]:
    """Run the requested kinds against the inventory. De-duplicates findings by identity."""
    want = set(include_kinds) if include_kinds else set(_ALL_DETECTORS.keys())
    seen: set[tuple[str, tuple[str, ...]]] = set()
    out: list[Finding] = []

    # Run each detector once (duplication fn emits both duplication + missing-defer-pattern,
    # so we only need to invoke it once even if both kinds are requested).
    invoked: set = set()
    for kind, fn in _ALL_DETECTORS.items():
        if kind not in want:
            continue
        if fn in invoked:
            continue
        invoked.add(fn)
        try:
            findings = fn(inventory)  # type: ignore[arg-type]
        except TypeError:
            # zip-divergence takes an extra root arg; call without it uses default
            findings = fn(inventory)  # type: ignore[arg-type]
        for f in findings:
            if f.kind not in want:
                continue
            if f.identity in seen:
                continue
            seen.add(f.identity)
            out.append(f)
    return out
