import { describe, it, expect } from 'vitest';
import JSZip from 'jszip';
import { parseSkillBundle, buildSkillBundle, isTextPath, findMissingSkillFiles } from './bundle';

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

describe('parseSkillBundle nested (Anthropic layout)', () => {
	it('strips the top-level skill directory and returns root-relative paths', async () => {
		const file = await makeSkillFile({
			'intermax-docx/SKILL.md': '---\nname: intermax-docx\n---\nbody',
			'intermax-docx/assets/template.docx': new Uint8Array([1, 2, 3]),
			'intermax-docx/assets/background.png': new Uint8Array([4, 5, 6]),
			'intermax-docx/.DS_Store': new Uint8Array([0]),
			'__MACOSX/intermax-docx/._SKILL.md': new Uint8Array([0])
		});
		const result = await parseSkillBundle(file);
		expect(result.skillMd).toContain('name: intermax-docx');
		// Prefix stripped; macOS junk (.DS_Store, __MACOSX) skipped.
		expect(result.files.map((f) => f.path).sort()).toEqual([
			'assets/background.png',
			'assets/template.docx'
		]);
	});
});

describe('findMissingSkillFiles', () => {
	const SKILL_MD = [
		'---',
		'name: intermax-docx',
		'---',
		'Fill the template at `assets/template.docx` with the cover `assets/background.png`.',
		'Run `python /mnt/skills/public/docx/scripts/office/replace_text.py assets/template.docx`.',
		'It edits `word/document.xml` internally.'
	].join('\n');

	it('returns nothing when every referenced path is present', () => {
		const files = [
			{ path: 'assets/template.docx', blob: new Blob([]) },
			{ path: 'assets/background.png', blob: new Blob([]) }
		];
		expect(findMissingSkillFiles(SKILL_MD, files)).toEqual([]);
	});

	it('flags a flat-packaged bundle (files at root, SKILL.md says assets/)', () => {
		const files = [
			{ path: 'template.docx', blob: new Blob([]) },
			{ path: 'background.png', blob: new Blob([]) }
		];
		expect(findMissingSkillFiles(SKILL_MD, files)).toEqual([
			'assets/background.png',
			'assets/template.docx'
		]);
	});

	it('ignores absolute /mnt/skills/public tooling and in-.docx word/ paths', () => {
		// Even with NO bundle files, the public tooling path and word/document.xml
		// must not be reported — only the two assets/ references are.
		expect(findMissingSkillFiles(SKILL_MD, [])).toEqual([
			'assets/background.png',
			'assets/template.docx'
		]);
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

describe('buildSkillBundle nested (Anthropic layout)', () => {
	it('wraps entries under rootDir and parse() strips it back to root-relative', async () => {
		const blob = await buildSkillBundle(
			'---\nname: intermax-docx\n---\nbody',
			[{ path: 'assets/template.docx', blob: new Blob([new Uint8Array([1, 2, 3])]) }],
			'intermax-docx'
		);
		// Raw zip carries the wrapping <skill-name>/ directory (what Claude expects).
		const zip = await JSZip.loadAsync(await blob.arrayBuffer());
		expect(Object.keys(zip.files)).toContain('intermax-docx/SKILL.md');
		expect(Object.keys(zip.files)).toContain('intermax-docx/assets/template.docx');
		// And our own parser strips it back to root-relative on re-import.
		const parsed = await parseSkillBundle(new File([blob], 'intermax-docx.skill'));
		expect(parsed.skillMd).toContain('name: intermax-docx');
		expect(parsed.files.map((f) => f.path)).toEqual(['assets/template.docx']);
	});
});
