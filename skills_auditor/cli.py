"""
`audit-skills` console entry point. Defaults to dry-run.

Usage:
    audit-skills            # dry-run; prints what would change in Notion
    audit-skills --commit   # actually write / verify / supersede
    audit-skills --kind duplication --kind orphan   # restrict kinds
"""

from __future__ import annotations

import argparse
import sys

from skills_auditor import report, scan


def main() -> int:
    parser = argparse.ArgumentParser(prog="audit-skills", description=__doc__)
    parser.add_argument(
        "--commit",
        action="store_true",
        help="Actually write to Notion (default is dry-run).",
    )
    parser.add_argument(
        "--kind",
        action="append",
        dest="kinds",
        metavar="KIND",
        help="Restrict to one or more finding kinds. Repeatable.",
    )
    args = parser.parse_args()

    findings = scan(include_kinds=args.kinds)
    summary = report(findings, dry_run=not args.commit)
    if summary.dry_run:
        return 0
    return 0 if summary.skipped == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
