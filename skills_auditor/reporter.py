"""
Reconciles detected findings with Notion records (via agent_memory) and returns
a ReportSummary. Phase 1 is hardcoded to type='reference', scope=['global'],
source_agent='skills-auditor' (spec §7 governance).
"""

from __future__ import annotations

import logging
import sys
from typing import Iterable

from skills_auditor.models import Finding, ReportSummary

logger = logging.getLogger("skills_auditor")

SOURCE_AGENT = "skills-auditor"
FINDING_TYPE = "reference"
FINDING_SCOPE = ["global"]
NAME_PREFIX = "skills-audit-"


def _import_agent_memory():
    """Imported lazily so `scan()` alone works with no Notion deps configured."""
    try:
        import agent_memory  # noqa: F401

        return agent_memory
    except ImportError as e:
        raise RuntimeError(
            "agent-memory not installed. Install with: "
            "pip install -e ~/ClaudeProjects/agent-memory"
        ) from e


def _fetch_existing_skills_audit_memories(am) -> dict[str, "am.Memory"]:
    """Return all active skills-audit-* memories in global scope, keyed by Name.

    First-run bootstrap: if the Notion `Source agent` select does not yet have
    a `skills-auditor` option, the filtered read errors with validation_error.
    That simply means "no prior findings exist" — Notion auto-creates the option
    on the first successful `write()`. Treat as an empty existing set.
    """
    try:
        mems = am.read(scope="global", type_filter=FINDING_TYPE, source_agent=SOURCE_AGENT)
    except am.MemoryError as e:
        msg = str(e)
        if SOURCE_AGENT in msg and "not found" in msg:
            logger.info(
                "first run: 'skills-auditor' not yet in Notion Source agent select "
                "— treating as empty existing set (will be created on first write)"
            )
            return {}
        logger.error("agent_memory.read failed: %s", e)
        raise
    return {m.name: m for m in mems if m.name.startswith(NAME_PREFIX)}


def _print_dry_run(
    to_create: list[Finding],
    to_verify: list[tuple[Finding, str]],
    to_supersede: list[str],
) -> None:
    print("=" * 72)
    print(f"skills-auditor: dry-run report")
    print("=" * 72)
    print(f"new findings to write          : {len(to_create)}")
    print(f"existing findings to re-verify : {len(to_verify)}")
    print(f"previously-recorded, now gone  : {len(to_supersede)}")
    print("-" * 72)
    for f in to_create:
        print(f"  [CREATE] {f.slug}")
        print(f"           kind={f.kind} severity={f.severity}")
        print(f"           {f.what[:120]}{'…' if len(f.what) > 120 else ''}")
    for f, _name in to_verify:
        print(f"  [VERIFY] {f.slug}")
    for n in to_supersede:
        print(f"  [SUPERSEDE] {n}")
    print("=" * 72)


def report(
    findings: Iterable[Finding],
    dry_run: bool = True,
) -> ReportSummary:
    """Reconcile `findings` against Notion records via agent_memory.

    Flow:
      - new finding (slug not in Notion)        → agent_memory.write()
      - finding already recorded, still detected → agent_memory.verify()
      - previously-recorded, NOT in this run    → agent_memory.supersede(source_agent='skills-auditor')

    Idempotent: re-running with no disk changes ⇒ all verifies, zero writes.
    If agent_memory is unreachable, prints findings to stdout and exits non-zero.
    """
    findings = list(findings)
    summary = ReportSummary(dry_run=dry_run)

    try:
        am = _import_agent_memory()
    except RuntimeError as e:
        logger.error(str(e))
        _print_local_fallback(findings)
        sys.exit(2)

    # Read existing skills-audit-* memories once
    try:
        existing = _fetch_existing_skills_audit_memories(am)
    except Exception as e:  # am.MemoryError or network
        logger.error("Notion unreachable: %s", e)
        _print_local_fallback(findings)
        sys.exit(2)

    current_slugs = {f.slug: f for f in findings}
    to_create = [f for f in findings if f.slug not in existing]
    to_verify = [(f, f.slug) for f in findings if f.slug in existing]
    to_supersede = [name for name in existing.keys() if name not in current_slugs]

    if dry_run:
        _print_dry_run(to_create, to_verify, to_supersede)
        summary.created = len(to_create)
        summary.verified = len(to_verify)
        summary.superseded = len(to_supersede)
        return summary

    # Live writes
    for f in to_create:
        try:
            am.write(
                name=f.slug,
                description=f.description,
                content=f.render_content(),
                type=FINDING_TYPE,
                scope=FINDING_SCOPE,
                source_agent=SOURCE_AGENT,
            )
            summary.created += 1
            logger.info("wrote finding %s", f.slug)
        except Exception as e:
            logger.error("write failed for %s: %s", f.slug, e)
            summary.skipped += 1

    for f, name in to_verify:
        try:
            am.verify(name, source_agent=SOURCE_AGENT)
            summary.verified += 1
        except Exception as e:
            logger.error("verify failed for %s: %s", name, e)
            summary.skipped += 1

    for name in to_supersede:
        try:
            am.supersede(name, source_agent=SOURCE_AGENT)
            summary.superseded += 1
            logger.info("superseded %s (not detected this run)", name)
        except Exception as e:
            logger.error("supersede failed for %s: %s", name, e)
            summary.skipped += 1

    print(
        f"skills-auditor: wrote {summary.created} / verified {summary.verified} / "
        f"superseded {summary.superseded} / skipped {summary.skipped}"
    )
    return summary


def _print_local_fallback(findings: list[Finding]) -> None:
    """Print findings to stdout so a CI run can still surface them before failing."""
    print("=" * 72)
    print("skills-auditor: Notion write unavailable — findings below")
    print("=" * 72)
    for f in findings:
        print(f"\n## {f.slug}  [{f.kind}, {f.severity}]")
        print(f.render_content())
