import { describe, it, expect } from 'vitest';
import JSZip from 'jszip';
import {
	parseSkillBundle,
	buildSkillBundle,
	findMissingSkillFiles,
	parseSkillMetadata
} from './assistantBundleParser';

async function makeSkillFile(entries: Record<string, string | Uint8Array>): Promise<File> {
	const zip = new JSZip();
	for (const [path, content] of Object.entries(entries)) zip.file(path, content);
	const blob = await zip.generateAsync({ type: 'blob' });
	return new File([blob], 'test.skill');
}

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
			'outline-docx/SKILL.md': '---\nname: outline-docx\n---\nbody',
			'outline-docx/assets/template.docx': new Uint8Array([1, 2, 3]),
			'outline-docx/assets/background.png': new Uint8Array([4, 5, 6]),
			'outline-docx/.DS_Store': new Uint8Array([0]),
			'__MACOSX/outline-docx/._SKILL.md': new Uint8Array([0])
		});
		const result = await parseSkillBundle(file);
		expect(result.skillMd).toContain('name: outline-docx');
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
		'name: outline-docx',
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
			'---\nname: outline-docx\n---\nbody',
			[{ path: 'assets/template.docx', blob: new Blob([new Uint8Array([1, 2, 3])]) }],
			'outline-docx'
		);
		// Raw zip carries the wrapping <skill-name>/ directory (what Claude expects).
		const zip = await JSZip.loadAsync(await blob.arrayBuffer());
		expect(Object.keys(zip.files)).toContain('outline-docx/SKILL.md');
		expect(Object.keys(zip.files)).toContain('outline-docx/assets/template.docx');
		// And our own parser strips it back to root-relative on re-import.
		const parsed = await parseSkillBundle(new File([blob], 'outline-docx.skill'));
		expect(parsed.skillMd).toContain('name: outline-docx');
		expect(parsed.files.map((f) => f.path)).toEqual(['assets/template.docx']);
	});
});

// [Gradient] Imports use one normalized zip request, preserving every file's bytes.
describe('assistant bundle import', () => {
	it.each(['zip', 'skill'])('imports a Claude .%s export directly', async (extension) => {
		const archive = await makeSkillFile({
			'outline/SKILL.md':
				'---\nname: outline\ndescription: >-\n  Draft a useful\n  outline.\n---\nBody',
			'outline/assets/template.bin': new Uint8Array([0, 255, 127]),
			'outline/scripts/run.py': 'print("example")'
		});
		const parsed = await parseSkillBundle(new File([archive], `outline.${extension}`));
		const bundle = await buildSkillBundle(parsed.skillMd, parsed.files);
		const zip = await JSZip.loadAsync(await bundle.arrayBuffer());
		expect(await zip.file('assets/template.bin')!.async('uint8array')).toEqual(
			new Uint8Array([0, 255, 127])
		);
		expect(await zip.file('scripts/run.py')!.async('string')).toBe('print("example")');
		expect(parseSkillMetadata(parsed.skillMd)).toEqual({
			name: 'outline',
			description: 'Draft a useful outline.'
		});
	});

	it('refuses multiple roots and deeply nested skills', async () => {
		const bundles: Record<string, string>[] = [
			{ 'outline/SKILL.md': 'body', 'other/data.txt': 'other' },
			{ 'one/two/SKILL.md': 'body' }
		];
		for (const entries of bundles) {
			await expect(parseSkillBundle(await makeSkillFile(entries))).rejects.toThrow('NO_SKILL_MD');
		}
	});

	it('rejects paths JSZip would sanitize', async () => {
		const file = await makeSkillFile({ 'SKILL.md': 'body', '../secret.txt': 'secret' });
		await expect(parseSkillBundle(file)).rejects.toThrow('INVALID_PATH');
	});

	it('reads only string metadata from frontmatter', () => {
		expect(parseSkillMetadata('plain body')).toEqual({});
		expect(parseSkillMetadata('---\nname: 23\ndescription: [a, b]\n---\nbody')).toEqual({
			name: undefined,
			description: undefined
		});
	});
});
