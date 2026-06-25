# `.skill` Bundle Import/Export Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let users import an Anthropic/Claude `.skill` bundle (zip of `SKILL.md` + `assets/`/`scripts/`) into OWUI and export an OWUI skill back out as a `.skill`, for Claude↔soev portability.

**Architecture:** 100% frontend (JSZip, already a dependency). A new pure util (`src/lib/utils/skills/bundle.ts`) does zip parse/build; `Skills.svelte` wires import (extend `accept`, unzip → create skill with verbatim `SKILL.md` as `content` → attach each file) and export (`SkillMenu` item → fetch skill+files → zip → `saveAs`). Frontmatter is preserved via `content`-verbatim (no `meta` change, no backend change).

**Tech Stack:** SvelteKit 5, TypeScript, JSZip `^3.10.1`, file-saver, Vitest.

## Global Constraints

- **Additive only** — modify only the already-custom skills feature files; no backend routes, no schema/migration, no upstream files. Rides the existing `FEATURE_SKILLS` flag.
- **i18n parity** — every new user-facing string MUST be added to BOTH `src/lib/i18n/locales/en-US/translation.json` and `src/lib/i18n/locales/nl-NL/translation.json`, keys in alphabetical order.
- **No new dependency** — JSZip `^3.10.1` is already in `package.json`.
- **Frontmatter preserved via `content`** — store the verbatim `SKILL.md` as the skill's `content`; `meta` stays `{ tags: [] }`. Do NOT put frontmatter in `meta`.
- **`id = slugify(name)`** on import (matches `SkillEditor` create behavior).
- **Imported skills are inactive** (`is_active: false`) so scripts are reviewed before activation.
- TypeScript strict; follow existing patterns in `src/lib/apis/skills/index.ts` and `Skills.svelte`.

---

### Task 1: Skill-bundle zip util (`bundle.ts`) + unit tests

**Files:**
- Create: `src/lib/utils/skills/bundle.ts`
- Test: `src/lib/utils/skills/bundle.test.ts`

**Interfaces:**
- Produces:
  - `isTextPath(path: string): boolean`
  - `interface SkillBundleFile { path: string; blob: Blob }`
  - `interface ParsedSkillBundle { skillMd: string; files: SkillBundleFile[] }`
  - `parseSkillBundle(file: File | Blob): Promise<ParsedSkillBundle>` — throws `Error('NO_SKILL_MD')` if no root `SKILL.md`
  - `buildSkillBundle(skillMd: string, files: SkillBundleFile[]): Promise<Blob>`

- [x] **Step 1: Write the failing tests**

Create `src/lib/utils/skills/bundle.test.ts`:

```ts
import { describe, it, expect } from 'vitest';
import JSZip from 'jszip';
import { parseSkillBundle, buildSkillBundle, isTextPath } from './bundle';

async function makeSkillFile(entries: Record<string, string | Uint8Array>): Promise<File> {
	const zip = new JSZip();
	for (const [path, content] of Object.entries(entries)) zip.file(path, content);
	const blob = await zip.generateAsync({ type: 'blob' });
	return new File([blob], 'test.skill');
}

describe('isTextPath', () => {
	it('treats known text extensions as text', () => {
		expect(isTextPath('scripts/run.py')).toBe(true);
		expect(isTextPath('SKILL.md')).toBe(true);
		expect(isTextPath('a/b/notes.txt')).toBe(true);
	});
	it('treats unknown/binary extensions as binary', () => {
		expect(isTextPath('assets/template.docx')).toBe(false);
		expect(isTextPath('assets/logo.png')).toBe(false);
		expect(isTextPath('noext')).toBe(false);
	});
});

describe('parseSkillBundle', () => {
	it('extracts SKILL.md and non-SKILL.md files, skipping directories', async () => {
		const file = await makeSkillFile({
			'SKILL.md': '---\nname: test\n---\nbody',
			'scripts/run.py': 'print(1)',
			'assets/x.bin': new Uint8Array([1, 2, 3])
		});
		const result = await parseSkillBundle(file);
		expect(result.skillMd).toContain('name: test');
		expect(result.files.map((f) => f.path).sort()).toEqual(['assets/x.bin', 'scripts/run.py']);
	});
	it('throws NO_SKILL_MD when SKILL.md is missing', async () => {
		const file = await makeSkillFile({ 'scripts/run.py': 'print(1)' });
		await expect(parseSkillBundle(file)).rejects.toThrow('NO_SKILL_MD');
	});
});

describe('buildSkillBundle round-trip', () => {
	it('parse(build(x)) preserves SKILL.md and file paths', async () => {
		const blob = await buildSkillBundle('---\nname: rt\n---\nbody', [
			{ path: 'scripts/run.py', blob: new Blob(['print(1)']) }
		]);
		const parsed = await parseSkillBundle(new File([blob], 'rt.skill'));
		expect(parsed.skillMd).toContain('name: rt');
		expect(parsed.files.map((f) => f.path)).toEqual(['scripts/run.py']);
	});
});
```

- [x] **Step 2: Run the tests to verify they fail**

Run: `npm run test:frontend -- src/lib/utils/skills/bundle.test.ts`
Expected: FAIL — `Failed to resolve import "./bundle"` / module not found.

- [x] **Step 3: Implement the util** _(deviation: convert Blob inputs to ArrayBuffer before handing to JSZip — `JSZip.loadAsync(await file.arrayBuffer())` and `zip.file(path, await blob.arrayBuffer())`. JSZip's Blob-input path uses FileReader, which is browser-only; the vitest `node` env has no FileReader, so the original code failed all JSZip tests. Public interface unchanged.)_

Create `src/lib/utils/skills/bundle.ts`:

```ts
import JSZip from 'jszip';

const SKILL_MD = 'SKILL.md';

// Extensions read as UTF-8 text and stored inline. Everything else is uploaded
// as a binary File. Mirrors the backend _TEXT_EXTENSIONS allowlist
// (backend/open_webui/utils/skill_bundles.py); when unsure, treat as binary
// (reading binary as UTF-8 would corrupt it; storing text as a File is harmless).
const TEXT_EXTENSIONS = new Set([
	'md', 'markdown', 'txt', 'py', 'js', 'ts', 'jsx', 'tsx', 'json', 'yaml', 'yml',
	'toml', 'ini', 'cfg', 'conf', 'sh', 'bash', 'zsh', 'html', 'htm', 'css', 'scss',
	'sass', 'xml', 'csv', 'rst', 'tex', 'sql', 'r', 'rb', 'java', 'c', 'cpp', 'h',
	'hpp', 'cs', 'go', 'rs', 'swift', 'kt', 'php', 'lua', 'tf', 'hcl'
]);

export interface SkillBundleFile {
	path: string;
	blob: Blob;
}

export interface ParsedSkillBundle {
	skillMd: string;
	files: SkillBundleFile[];
}

export const isTextPath = (path: string): boolean => {
	const ext = path.includes('.') ? path.split('.').pop()!.toLowerCase() : '';
	return TEXT_EXTENSIONS.has(ext);
};

export const parseSkillBundle = async (file: File | Blob): Promise<ParsedSkillBundle> => {
	const zip = await JSZip.loadAsync(file);
	const skillMdEntry = zip.file(SKILL_MD);
	if (!skillMdEntry) {
		throw new Error('NO_SKILL_MD');
	}
	const skillMd = await skillMdEntry.async('string');

	const files: SkillBundleFile[] = [];
	for (const entry of Object.values(zip.files)) {
		if (entry.dir || entry.name === SKILL_MD) continue;
		const blob = await entry.async('blob');
		files.push({ path: entry.name, blob });
	}
	return { skillMd, files };
};

export const buildSkillBundle = async (
	skillMd: string,
	files: SkillBundleFile[]
): Promise<Blob> => {
	const zip = new JSZip();
	zip.file(SKILL_MD, skillMd);
	for (const f of files) zip.file(f.path, f.blob);
	return zip.generateAsync({ type: 'blob' });
};
```

- [x] **Step 4: Run the tests to verify they pass**

Run: `npm run test:frontend -- src/lib/utils/skills/bundle.test.ts`
Expected: PASS (all describe blocks green). _(5 passed.)_

- [x] **Step 5: Commit** _(committed 40deb746a)_

```bash
git add src/lib/utils/skills/bundle.ts src/lib/utils/skills/bundle.test.ts
git commit -m "feat(skills): add .skill bundle zip parse/build util"
```

---

### Task 2: Import `.skill` in `Skills.svelte`

**Files:**
- Modify: `src/lib/components/workspace/Skills.svelte`
- Modify: `src/lib/i18n/locales/en-US/translation.json`
- Modify: `src/lib/i18n/locales/nl-NL/translation.json`

**Interfaces:**
- Consumes: `parseSkillBundle`, `isTextPath` (Task 1); `createNewSkill`, `createSkillFile`, `createSkillFileInline`, `getSkills` (`$lib/apis/skills`); `uploadFile` (`$lib/apis/files`); `slugify`, `parseFrontmatter`, `formatSkillName` (`$lib/utils`).

- [x] **Step 1: Add imports** _(merged `slugify` into the existing `$lib/utils` import instead of a separate line, to avoid ESLint no-duplicate-imports)_

In `Skills.svelte`, the `$lib/apis/skills` import block is `import { … } from '$lib/apis/skills';` (lines 1–9). Add `createSkillFile` and `createSkillFileInline` to that list. Then add these imports immediately after line 10 (`import { capitalizeFirstLetter, parseFrontmatter, formatSkillName } from '$lib/utils';`):

```ts
	import { slugify } from '$lib/utils';
	import { uploadFile } from '$lib/apis/files';
	import { parseSkillBundle, isTextPath } from '$lib/utils/skills/bundle';
```

- [x] **Step 2: Add the `importSkillBundle` function** _(typed `fm` as `{ name?: string; description?: string }` to avoid new svelte-check errors)_

In `Skills.svelte`, immediately after the existing `exportHandler` function (ends at the line `};` after `saveAs(blob, \`skill-${_skill.id}-export-${Date.now()}.json\`);`), add:

```ts
	const importSkillBundle = async (file: File) => {
		let parsed;
		try {
			parsed = await parseSkillBundle(file);
		} catch (e) {
			toast.error($i18n.t('Not a valid skill bundle (no SKILL.md).'));
			return;
		}

		const fm = parseFrontmatter(parsed.skillMd);
		const baseName = file.name.replace(/\.skill$/, '');
		const name = formatSkillName(fm.name || baseName);

		let created;
		try {
			created = await createNewSkill(localStorage.token, {
				id: slugify(name),
				name,
				description: fm.description || '',
				content: parsed.skillMd,
				is_active: false,
				meta: { tags: [] },
				access_grants: []
			});
		} catch (e) {
			toast.error(`${e}`);
			return;
		}

		for (const bf of parsed.files) {
			try {
				if (isTextPath(bf.path)) {
					const text = await bf.blob.text();
					await createSkillFileInline(localStorage.token, created.id, {
						path: bf.path,
						content: text
					});
				} else {
					const filename = bf.path.split('/').pop() || bf.path;
					const uploaded = await uploadFile(
						localStorage.token,
						new File([bf.blob], filename),
						null,
						false
					);
					await createSkillFile(localStorage.token, created.id, {
						path: bf.path,
						file_id: uploaded.id
					});
				}
			} catch (e) {
				toast.error($i18n.t('Failed to import file: {{path}}', { path: bf.path }));
			}
		}

		toast.success($i18n.t('Skill imported successfully'));
		page = 1;
		loadSkillItems();
		_skills.set(await getSkills(localStorage.token));
	};
```

- [x] **Step 3: Wire the import branch + extend `accept`**

In the import `<input>` (currently `accept=".md,.json"`), change the attribute to:

```svelte
					accept=".md,.json,.skill"
```

In the same input's `on:change` handler, the dispatch is currently `if (ext === 'json') { … } else { …markdown… }`. Insert a `.skill` branch between them so it reads:

```ts
								if (ext === 'json') {
									// JSON import: create skills via API (unchanged)
									const reader = new FileReader();
									// … existing JSON block unchanged …
								} else if (ext === 'skill') {
									importSkillBundle(file);
								} else {
									// Markdown import (unchanged)
									const reader = new FileReader();
									// … existing markdown block unchanged …
								}
```

(Leave the existing JSON and markdown block bodies exactly as they are — only the `else if (ext === 'skill')` branch is added.)

- [x] **Step 4: Add i18n strings** _(en-US values left empty `""` per codebase convention = "use the key itself"; nl-NL got the Dutch text)_

In `src/lib/i18n/locales/en-US/translation.json`, add (alphabetical position):

```json
	"Failed to import file: {{path}}": "Failed to import file: {{path}}",
	"Not a valid skill bundle (no SKILL.md).": "Not a valid skill bundle (no SKILL.md).",
```

In `src/lib/i18n/locales/nl-NL/translation.json`, add (alphabetical position):

```json
	"Failed to import file: {{path}}": "Bestand importeren mislukt: {{path}}",
	"Not a valid skill bundle (no SKILL.md).": "Geen geldige skill-bundel (geen SKILL.md).",
```

(`Skill imported successfully` already exists — reuse it.)

- [x] **Step 5: Typecheck** _(no new errors from new logic; bundle.ts clean. Pre-existing systemic `i18n`-store + untyped-`fm` errors in the markdown handler are baseline.)_

Run: `npm run check`
Expected: no new TypeScript/svelte-check errors in `Skills.svelte` or `bundle.ts`.

- [x] **Step 6: Re-run unit tests (no regressions)** _(5 passed.)_

Run: `npm run test:frontend -- src/lib/utils/skills/bundle.test.ts`
Expected: PASS.

- [ ] **Step 7: Manual verification**

With `FEATURE_SKILLS=true` (staging or local dev): Workspace → Skills → **Import** → select a `.skill` (e.g. `intermax-docx.skill`, which has a binary `.docx` asset). Expected: a new (inactive) skill appears; open it → file tree shows `SKILL.md` content + `assets/…docx` + any `scripts/…`; the `.docx` downloads byte-identically via the content route. A bundle with no `SKILL.md` → error toast.

- [x] **Step 8: Commit** _(committed 08bdc661f)_

```bash
git add src/lib/components/workspace/Skills.svelte src/lib/i18n/locales/en-US/translation.json src/lib/i18n/locales/nl-NL/translation.json
git commit -m "feat(skills): import Anthropic .skill bundles"
```

---

### Task 3: Export `.skill` (`Skills.svelte` + `SkillMenu.svelte`)

**Files:**
- Modify: `src/lib/components/workspace/Skills.svelte`
- Modify: `src/lib/components/workspace/Skills/SkillMenu.svelte`
- Modify: `src/lib/i18n/locales/en-US/translation.json`
- Modify: `src/lib/i18n/locales/nl-NL/translation.json`

**Interfaces:**
- Consumes: `buildSkillBundle` (Task 1); `getSkillById`, `getSkillFileList`, `getSkillFileContentBlob` (`$lib/apis/skills`); `saveAs` (already imported in `Skills.svelte`).
- Produces: `exportBundleHandler` prop on `SkillMenu`.

- [x] **Step 1: Add imports**

In `Skills.svelte`, add `getSkillFileList` and `getSkillFileContentBlob` to the `$lib/apis/skills` import block, and add `buildSkillBundle` to the bundle-util import from Task 2 so it reads:

```ts
	import { parseSkillBundle, buildSkillBundle, isTextPath } from '$lib/utils/skills/bundle';
```

- [x] **Step 2: Add the `exportBundleHandler` function** _(DEVIATION — frontend-only, no page loop. The `GET /id/{id}/files` route has no pagination param and is hard-capped at 30 files; the page loop would refetch page 1 (duplicating + missing files). Per user decision, fetch once, export the returned files, and `toast.warning` when `total > items.length` instead of silently truncating.)_

In `Skills.svelte`, immediately after `importSkillBundle` (from Task 2), add:

```ts
	const exportBundleHandler = async (skill) => {
		const _skill = await getSkillById(localStorage.token, skill.id).catch((error) => {
			toast.error(`${error}`);
			return null;
		});
		if (!_skill) return;

		// Page through the file list so large bundles aren't silently truncated.
		const items: { path: string }[] = [];
		let pageNum = 1;
		while (true) {
			const res = await getSkillFileList(localStorage.token, _skill.id, pageNum).catch(() => null);
			const batch = res?.items ?? [];
			items.push(...batch);
			if (batch.length === 0 || items.length >= (res?.total ?? items.length)) break;
			pageNum += 1;
		}

		const bundleFiles = [];
		for (const item of items) {
			try {
				const blob = await getSkillFileContentBlob(localStorage.token, _skill.id, item.path);
				bundleFiles.push({ path: item.path, blob });
			} catch (e) {
				toast.error($i18n.t('Failed to export file: {{path}}', { path: item.path }));
			}
		}

		const blob = await buildSkillBundle(_skill.content || '', bundleFiles);
		saveAs(blob, `${_skill.name}.skill`);
	};
```

- [x] **Step 3: Add the `exportBundleHandler` prop to `SkillMenu`**

In `SkillMenu.svelte`, add the prop next to the other handler props (after `export let exportHandler: Function;`):

```ts
	export let exportBundleHandler: Function;
```

- [x] **Step 4: Add the "Export bundle" menu item**

In `SkillMenu.svelte`, immediately after the existing Export button block (the `</button>` that closes the `Export` item, before the `<hr … />`), add:

```svelte
				<button
					class="select-none flex gap-2 items-center px-3 py-1.5 text-sm cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800 rounded-xl w-full"
					on:click={() => {
						exportBundleHandler();
					}}
				>
					<Download />
					<div class="flex items-center">{$i18n.t('Export bundle')}</div>
				</button>
```

(Reuses the already-imported `Download` icon. Place it inside the same `{#if $user?.role === 'admin' || $user?.permissions?.workspace?.skills}` guard as the JSON Export item.)

- [x] **Step 5: Wire the prop in `Skills.svelte`**

In `Skills.svelte` where `<SkillMenu … />` is rendered, add alongside `exportHandler={() => { exportHandler(skill); }}`:

```svelte
											exportBundleHandler={() => {
												exportBundleHandler(skill);
											}}
```

- [x] **Step 6: Add i18n strings** _(added a 3rd key for the truncation warning — `"Only the first {{count}} of {{total}} files were exported."` (nl: "Alleen de eerste {{count}} van {{total}} bestanden zijn geëxporteerd."). en-US values empty per convention.)_

In `src/lib/i18n/locales/en-US/translation.json` (alphabetical):

```json
	"Export bundle": "Export bundle",
	"Failed to export file: {{path}}": "Failed to export file: {{path}}",
```

In `src/lib/i18n/locales/nl-NL/translation.json` (alphabetical):

```json
	"Export bundle": "Bundel exporteren",
	"Failed to export file: {{path}}": "Bestand exporteren mislukt: {{path}}",
```

- [x] **Step 7: Typecheck** _(only new error is the untyped `skill` param on `exportBundleHandler` (line 193), identical to the 4 sibling handlers — kept for local consistency. Export logic, SkillMenu, and bundle.ts all clean.)_

Run: `npm run check`
Expected: no new errors.

- [ ] **Step 8: Manual round-trip verification**

Workspace → Skills → a skill's `⋯` menu → **Export bundle** → a `<name>.skill` downloads. Re-import it (Task 2 path) → a new skill with identical `SKILL.md` content + identical file tree (binary assets byte-identical). Confirm the `.json` Export still works unchanged.

- [x] **Step 9: Commit** _(committed 1458c2b1a)_

```bash
git add src/lib/components/workspace/Skills.svelte src/lib/components/workspace/Skills/SkillMenu.svelte src/lib/i18n/locales/en-US/translation.json src/lib/i18n/locales/nl-NL/translation.json
git commit -m "feat(skills): export skills as .skill bundles"
```

---

## Self-Review

- **Spec coverage:** import (Task 2) ✓, export (Task 3) ✓, binary round-trip (uploadFile+createSkillFile / getSkillFileContentBlob) ✓, frontmatter preservation via content-verbatim (Global Constraints + Task 2 step 2) ✓, i18n parity (Tasks 2/3 i18n steps) ✓, additive/no-backend (Global Constraints) ✓. Option B (no `meta.frontmatter`) reflected — `meta: { tags: [] }` only.
- **Placeholders:** none — every code/step is concrete.
- **Type consistency:** `parseSkillBundle`/`buildSkillBundle`/`isTextPath`/`SkillBundleFile` defined in Task 1 and consumed verbatim in Tasks 2/3; API fns match `src/lib/apis/skills/index.ts` signatures (`createSkillFileInline({path, content})`, `createSkillFile({path, file_id})`, `getSkillFileList(token,id,page)`, `getSkillFileContentBlob(token,id,path)`); `uploadFile(token, File, null, false)` per `src/lib/apis/files`.
- **Pagination:** export loops by `total` so no silent file truncation.
