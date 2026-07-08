"""
resync-skill-zip <name> — re-zip a global skill from its disk directory.

Disk is authoritative; the `.zip` is a stale export artifact used only as the
manual sync bridge to the claude.ai skill twin. This helper rebuilds the zip
from the current disk contents so the next audit run auto-supersedes any
`zip-divergence` finding for that skill.

Zip layout matches the existing exports:
    <name>/
    <name>/SKILL.md
    <name>/<any-other-files...>

After running this, you still have to upload the new zip to claude.ai manually
(per feedback_claude_ai_skill_twin_sync.md). This tool only reconciles the
local zip — the web twin is a separate surface.
"""

from __future__ import annotations

import argparse
import shutil
import sys
import zipfile
from pathlib import Path

GLOBAL_SKILLS_ROOT = Path.home() / ".claude" / "skills"


def resync(name: str, root: Path = GLOBAL_SKILLS_ROOT, backup: bool = True) -> Path:
    """Rebuild `<root>/<name>.zip` from `<root>/<name>/`. Returns path to the new zip.

    Raises FileNotFoundError if the source directory doesn't exist.
    If `backup` is True and an existing zip is present, copy it to `<name>.zip.bak`
    before overwriting — safety net for voice-sensitive files.
    """
    src_dir = root / name
    if not src_dir.is_dir():
        raise FileNotFoundError(
            f"no source directory at {src_dir}. Expected a global skill at "
            f"~/.claude/skills/{name}/"
        )
    skill_md = src_dir / "SKILL.md"
    if not skill_md.is_file():
        raise FileNotFoundError(
            f"{src_dir} has no SKILL.md — this doesn't look like a skill directory."
        )

    zip_path = root / f"{name}.zip"
    if backup and zip_path.exists():
        backup_path = zip_path.with_suffix(".zip.bak")
        shutil.copy2(zip_path, backup_path)
        print(f"backup: {zip_path.name} → {backup_path.name}")

    # Write into a tmp path, then atomic rename so a crash never leaves a half-zip
    tmp_path = zip_path.with_suffix(".zip.tmp")
    with zipfile.ZipFile(tmp_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        # Directory entry first (matches existing zip layout)
        zf.writestr(f"{name}/", "")
        for entry in sorted(src_dir.rglob("*")):
            if entry.is_dir():
                # only write explicit dir entries for non-root dirs
                rel = entry.relative_to(src_dir)
                if str(rel) == ".":
                    continue
                zf.writestr(f"{name}/{rel}/", "")
                continue
            rel = entry.relative_to(src_dir)
            zf.write(entry, arcname=f"{name}/{rel}")

    tmp_path.replace(zip_path)
    print(f"wrote: {zip_path}")
    return zip_path


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="resync-skill-zip",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "names",
        nargs="+",
        help="Skill names to re-zip, e.g. `cara-voice cara-cover-letter`. "
        "No paths — name only.",
    )
    parser.add_argument(
        "--no-backup",
        action="store_true",
        help="Skip the .zip.bak safety copy (default: keep a backup).",
    )
    args = parser.parse_args()

    exit_code = 0
    for name in args.names:
        try:
            resync(name, backup=not args.no_backup)
        except FileNotFoundError as e:
            print(f"error: {e}", file=sys.stderr)
            exit_code = 1
    if exit_code == 0:
        print(
            "\nnext: upload the new zip(s) to claude.ai to update the web skill twin.\n"
            "then re-run `audit-skills --commit` — stale zip-divergence findings will supersede."
        )
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
