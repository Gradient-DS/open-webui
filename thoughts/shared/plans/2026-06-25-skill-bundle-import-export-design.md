# Design — `.skill` bundle import/export (Anthropic-format interop)

**Date:** 2026-06-25
**Author:** @lexlubbers (with Claude)
**Branch:** `feat/skill-bundle-import-export` (off `dev`)
**Status:** Approved design — ready for implementation plan

## Goal

Let users import an Anthropic/Claude **Agent Skill** bundle (`.skill`) into OWUI and export an OWUI skill back out as a `.skill`, so skills are portable between Claude and soev. Purely additive on the existing (custom-fork) skills feature: **frontend-only (JSZip), no backend routes, no new dependency, no new feature flag.**

## Background / current state

- A `.skill` file is a **zip**: `SKILL.md` at the root + arbitrary files under `assets/`, `scripts/`, etc. `SKILL.md` carries YAML frontmatter (`name`, `description`, and possibly extra keys like `license`, `allowed-tools`).
- OWUI's skill model (`backend/open_webui/models/skills.py`): `name` (unique), `description`, `content`, `meta` (JSON), plus a **path-tree of files**. Each skill file is a row in `skill_files` backed by a real `File` object (binary-safe; raw bytes streamed via `GET /api/v1/skills/id/{id}/files/content`). Files are attached **after** the skill is created.
- Today's import (`src/lib/components/workspace/Skills.svelte`, input `accept=".md,.json"`):
  - `.json` → `createNewSkill()` directly, then refresh list.
  - `.md` → `parseFrontmatter()` → prefill editor via `sessionStorage` → `/workspace/skills/create`.
- Today's export (`exportHandler` in `Skills.svelte`, triggered from `SkillMenu.svelte`) → `saveAs([_skill], 'skill-<id>-export-<ts>.json')`.
- `jszip@^3.10.1` is already a dependency. `parseFrontmatter` + `formatSkillName` already exist in `src/lib/utils/index.ts`.

## Scope

In scope: `.skill` import, `.skill` export, frontmatter round-trip preservation, binary asset round-trip, en-US + nl-NL i18n.
Out of scope: backend changes, a new feature flag (rides `FEATURE_SKILLS`), execution security (unchanged — gated by `skill_execution_enabled` + gVisor, tracked separately), auto-rename on name collision.

## Design

### Bundle utilities (new, isolated, testable)

New module `src/lib/utils/skills/bundle.ts` with two pure-ish functions so the zip logic is unit-testable independent of Svelte/UI:

- `parseSkillBundle(file: File): Promise<{ frontmatter: Record<string, unknown>; skillMd: string; files: { path: string; blob: Blob }[] }>`
  - JSZip-load; require a root `SKILL.md` (throw a typed error if absent).
  - `parseFrontmatter(skillMd)` → `frontmatter`.
  - Collect every other entry (skip directories and `SKILL.md`) as `{ path, blob }`, preserving the in-zip relative path.
- `buildSkillBundle(skillMd: string, files: { path: string; blob: Blob }[]): Promise<Blob>`
  - JSZip: write `SKILL.md` at root + each file at its path → `generateAsync({ type: 'blob' })`.

Frontmatter serialization for export: if a serializer exists in utils, reuse it; otherwise add a minimal `stringifyFrontmatter(obj): string` (YAML block) next to `parseFrontmatter`. (Plan to confirm.)

### Import flow (`Skills.svelte`)

1. Extend input `accept` to `.md,.json,.skill`.
2. New `ext === 'skill'` branch:
   a. `parseSkillBundle(file)`.
   b. Map: `name = formatSkillName(frontmatter.name || <filename w/o .skill>)`, `description = frontmatter.description || ''`, `content = skillMd` (full SKILL.md, mirroring the `.md` path). Preserve the **full frontmatter dict** in `meta.frontmatter`.
   c. `createNewSkill({ name, description, content, meta: { frontmatter }, is_active: true, access_grants: [] })` → `skill.id`. (Inactive/unshared by default so imported scripts are reviewed before use.)
   d. For each `files[]` entry: upload as a `File` (binary-safe Blob) and attach at `path`, reusing the **same add-file path SkillEditor uses** (plan to confirm exact helper: upload + `attach`, or a combined client fn). Sequential, with a progress toast for multi-file bundles.
   e. On success: refresh list + success toast (mirrors `.json` UX). On failure (e.g. unique-name conflict, missing `SKILL.md`): clear typed error toast; best-effort note if the skill was created but a file attach failed.
3. Reset `importInputElement.value`.

### Export flow (`SkillMenu.svelte` + `Skills.svelte`)

1. Add a menu item **"Export as bundle (.skill)"** alongside the existing JSON export (keep `.json` for OWUI-native round-trips).
2. Handler:
   a. Fetch skill (`getSkillById`) + file list (`getSkillFiles`) + each file's bytes (`getSkillFileContent`, binary-safe).
   b. Rebuild `SKILL.md`: merge current `name`/`description` over `meta.frontmatter` → serialize frontmatter → prepend to the SKILL.md body (derive body from `content`, stripping any existing frontmatter block to avoid duplication).
   c. `buildSkillBundle(skillMd, files)` → `saveAs(blob, \`${name}.skill\`)`.

### Error handling

- Missing `SKILL.md` → "Not a valid skill bundle (no SKILL.md)".
- Duplicate name (DB unique) → surface backend error toast (no auto-rename).
- Per-file attach failure during import → toast which path failed; skill remains with the files that succeeded (no silent partial success).

### Testing

- Unit (Vitest): `parseSkillBundle` (valid bundle, missing SKILL.md, nested paths, binary asset), `buildSkillBundle` (round-trip: parse→build→parse is stable), frontmatter round-trip preserves extra keys.
- Manual: import `intermax-docx.skill` (has a binary `.docx` asset) → skill appears with correct file tree + the docx is byte-identical via the content route → export → re-import → identical. Then the 23×19 calc skill loop end-to-end with execution on.

## i18n

New keys in both `src/lib/i18n/locales/en-US/translation.json` and `nl-NL/translation.json` (alphabetical): import/export-bundle labels, progress toast, and the error messages above.

## Merge-compatibility

All changes are additive and confined to already-custom fork files (the skills feature is a Gradient addition): one new util module, branches in `Skills.svelte`, a menu item in `SkillMenu.svelte`, i18n entries. No upstream files modified, no backend routes, no schema/migration. Rides the existing `FEATURE_SKILLS` flag.

## Open items for the implementation plan to pin down

1. The exact frontend helper SkillEditor uses to add a file to a skill (upload `File` + `attach` vs a combined client fn) — reuse it for import step (d).
2. Whether a frontmatter serializer already exists; if not, add a minimal one.
3. How `content` relates to frontmatter on existing skills (does the editor canonicalize/strip frontmatter on save?) — confirms the export body-derivation step.
