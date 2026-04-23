"""
Disk walker. Turns the filesystem into a list of SkillFile objects.

Two shapes of skills on disk:
- directory style: `<root>/<name>/SKILL.md` with YAML frontmatter (global + plugin convention)
- standalone:     `<root>/<name>.md`          with YAML frontmatter (in-repo convention,
                                               e.g. job-search-agent-v2/.claude/skills/cover-letter-drafter.md)

Both shapes are read the same way: parse frontmatter, extract H2/H3 headers,
record text body. No Notion calls happen in this module.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Iterable

from skills_auditor.models import SkillFile, Scope

logger = logging.getLogger("skills_auditor")

HOME = Path.home()
DEFAULT_GLOBAL_ROOT = HOME / ".claude" / "skills"
DEFAULT_PLUGIN_ROOT = HOME / ".claude" / "plugins"
DEFAULT_PROJECTS_ROOT = HOME / "ClaudeProjects"

FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", re.DOTALL)
HEADER_RE = re.compile(r"^(#{1,3})\s+(.+?)\s*$", re.MULTILINE)


def _read_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Hand-rolled minimal frontmatter parser — skill frontmatter is flat key: value.

    Returns (fields, body). Unknown/missing → empty dict + full text.
    """
    m = FRONTMATTER_RE.match(text)
    if not m:
        return {}, text
    raw, body = m.group(1), m.group(2)
    fields: dict[str, str] = {}
    for line in raw.splitlines():
        line = line.rstrip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        k, _, v = line.partition(":")
        fields[k.strip()] = v.strip().strip('"').strip("'")
    return fields, body


def _extract_headers(body: str) -> list[str]:
    return [m.group(2).strip() for m in HEADER_RE.finditer(body)]


def _skillfile_from_path(
    content_path: Path,
    scope: Scope,
    project: str | None,
    name_override: str | None = None,
) -> SkillFile | None:
    try:
        text = content_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        logger.warning("skipping unreadable skill file %s: %s", content_path, e)
        return None

    fields, body = _read_frontmatter(text)
    name = name_override or fields.get("name") or content_path.stem
    description = fields.get("description", "")
    headers = _extract_headers(body)
    # directory-style skills: path points at the dir, content_path at SKILL.md
    path = content_path.parent if content_path.name == "SKILL.md" else content_path
    return SkillFile(
        name=name,
        scope=scope,
        project=project,
        path=path,
        content_path=content_path,
        description=description,
        body=body,
        section_headers=headers,
    )


def scan_global(root: Path = DEFAULT_GLOBAL_ROOT) -> list[SkillFile]:
    """Walk ~/.claude/skills/*/SKILL.md."""
    skills: list[SkillFile] = []
    if not root.exists():
        return skills
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        skill_md = child / "SKILL.md"
        if skill_md.is_file():
            sf = _skillfile_from_path(skill_md, scope="global", project=None, name_override=child.name)
            if sf:
                skills.append(sf)
    return skills


def scan_projects(root: Path = DEFAULT_PROJECTS_ROOT) -> list[SkillFile]:
    """Walk ~/ClaudeProjects/*/.claude/skills/ for both dir-style and standalone skills."""
    skills: list[SkillFile] = []
    if not root.exists():
        return skills
    for proj in sorted(root.iterdir()):
        if not proj.is_dir() or proj.name.startswith("."):
            continue
        skills_dir = proj / ".claude" / "skills"
        if not skills_dir.is_dir():
            continue
        for entry in sorted(skills_dir.iterdir()):
            if entry.is_dir():
                skill_md = entry / "SKILL.md"
                if skill_md.is_file():
                    sf = _skillfile_from_path(
                        skill_md, scope="project", project=proj.name, name_override=entry.name
                    )
                    if sf:
                        skills.append(sf)
            elif entry.is_file() and entry.suffix == ".md":
                sf = _skillfile_from_path(entry, scope="project", project=proj.name)
                if sf:
                    skills.append(sf)
    return skills


def scan_plugins(root: Path = DEFAULT_PLUGIN_ROOT) -> list[SkillFile]:
    """Walk plugin skill directories. Skills live at any depth under `skills/` dirs."""
    skills: list[SkillFile] = []
    if not root.exists():
        return skills
    # Plugin layout is arbitrary. Heuristic: any `skills/<name>/SKILL.md` under root.
    for skill_md in root.rglob("skills/*/SKILL.md"):
        # plugin name = nearest ancestor directory containing `plugin.json` or top-level plugins/ dir child
        plugin_dir = skill_md.parents[2]  # .../<plugin-name>/skills/<skill-name>/SKILL.md
        sf = _skillfile_from_path(
            skill_md, scope="plugin", project=plugin_dir.name, name_override=skill_md.parent.name
        )
        if sf:
            skills.append(sf)
    return skills


def scan_disk(roots: Iterable[Path] | None = None) -> list[SkillFile]:
    """Full inventory across global, project, and plugin scopes.

    `roots` is accepted for future override use; Phase 1 ignores it in favor
    of the three hardcoded layout conventions. (The spec's `scan()` signature
    keeps `roots` as a forward-compat hook.)
    """
    inv = scan_global() + scan_projects() + scan_plugins()
    logger.info(
        "scanner: %d global, %d project, %d plugin skills found",
        sum(1 for s in inv if s.scope == "global"),
        sum(1 for s in inv if s.scope == "project"),
        sum(1 for s in inv if s.scope == "plugin"),
    )
    return inv
