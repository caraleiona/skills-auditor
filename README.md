# skills-auditor

Read-mostly, append-only scanner that detects drift across Claude Code skill
scopes (global `~/.claude/skills/`, in-repo `~/ClaudeProjects/*/.claude/skills/`,
and plugin `~/.claude/plugins/*/skills/`) and records findings as durable
references in the cross-agent Notion memory layer.

Built per `~/ClaudeProjects/agent-architecture/02-skills-auditor-spec.md`.
Depends on [`agent-memory`](https://github.com/caraleiona/agent-memory) ≥ 0.1.1.

## What it detects (Phase 1)

- `duplication` — in-repo skill covers ≥60% of a global skill's domain (Jaccard on description + section headers)
- `missing-defer-pattern` — duplication exists without an explicit "authoritative source" reference in the in-repo skill
- `stale-reference` — skill name referenced in `CLAUDE.md` / memory files but no file at that path
- `orphan` — in-repo skill exists but nothing references it
- `naming-collision` — same skill name across scopes
- `zip-divergence` — `<name>.zip` sitting next to `<name>/` with different contents

## What it never does

- Edits skill files (Phase 1 is report-only; auto-fix deferred to Phase 2)
- Writes findings anywhere other than the Notion `Agent Memory` DB via `agent_memory.write()`
- Calls `supersede()` except for its own `skills-audit-*` findings (narrow exception per agent-memory v0.1.1)

## Install

Editable local:

```bash
pip install -e ~/ClaudeProjects/skills-auditor
```

The package depends on a patched `agent-memory ≥ 0.1.1`. For local dev, install
the editable agent-memory first:

```bash
pip install -e ~/ClaudeProjects/agent-memory
```

## Use

Python API:

```python
from skills_auditor import scan, report, audit

findings = scan()                   # walk disk, return Finding objects
summary = report(findings, dry_run=True)   # print what would happen
summary = audit()                   # scan() then report(dry_run=False)
```

One-shot CLI:

```bash
audit-skills              # dry-run; prints findings
audit-skills --commit     # writes to Notion
```

## Finding identity

`(kind, sorted tuple of involved file paths)` → a short slug on the `Name`
field. Re-running the audit without disk changes produces zero `write()` calls
— only `verify()` on the still-active findings.

## Governance

- Hardcoded in `report()`: `type="reference"`, `scope=["global"]`, `source_agent="skills-auditor"`.
- Never reads or writes raw skill text (>80 chars) into finding bodies — paths and structural facts only.
- `dry_run=True` default; `--commit` must be explicit.

See the full spec for the rest (first-run plan, Phase 2 auto-fix rationale, governance rules).
