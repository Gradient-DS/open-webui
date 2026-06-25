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
