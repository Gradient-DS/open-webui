import { describe, it, expect } from 'vitest';
import { buildTree, isTextPath, languageForPath } from './utils';

describe('isTextPath', () => {
	it('treats common text/code extensions as text (case-insensitive)', () => {
		expect(isTextPath('guide.md')).toBe(true);
		expect(isTextPath('README.MARKDOWN')).toBe(true);
		expect(isTextPath('notes.txt')).toBe(true);
		expect(isTextPath('script.py')).toBe(true);
		expect(isTextPath('app.JS')).toBe(true);
		expect(isTextPath('types.ts')).toBe(true);
		expect(isTextPath('data.json')).toBe(true);
		expect(isTextPath('config.yaml')).toBe(true);
		expect(isTextPath('config.yml')).toBe(true);
		expect(isTextPath('table.csv')).toBe(true);
		expect(isTextPath('page.html')).toBe(true);
		expect(isTextPath('style.css')).toBe(true);
		expect(isTextPath('doc.xml')).toBe(true);
		expect(isTextPath('run.sh')).toBe(true);
		expect(isTextPath('query.sql')).toBe(true);
		expect(isTextPath('analysis.r')).toBe(true);
	});

	it('resolves the extension from a nested path', () => {
		expect(isTextPath('docs/sub/guide.md')).toBe(true);
		expect(isTextPath('assets/logo.png')).toBe(false);
	});

	it('treats binary extensions as non-text', () => {
		expect(isTextPath('image.png')).toBe(false);
		expect(isTextPath('photo.jpg')).toBe(false);
		expect(isTextPath('archive.zip')).toBe(false);
		expect(isTextPath('doc.pdf')).toBe(false);
		expect(isTextPath('model.ifc')).toBe(false);
	});

	it('treats names with no extension or empty as non-text', () => {
		expect(isTextPath('README')).toBe(false);
		expect(isTextPath('')).toBe(false);
		expect(isTextPath('md')).toBe(false);
	});
});

describe('languageForPath', () => {
	it('maps known extensions to CodeMirror language aliases', () => {
		expect(languageForPath('script.py')).toBe('python');
		expect(languageForPath('app.js')).toBe('javascript');
		expect(languageForPath('app.jsx')).toBe('javascript');
		expect(languageForPath('types.ts')).toBe('typescript');
		expect(languageForPath('types.tsx')).toBe('typescript');
		expect(languageForPath('data.json')).toBe('json');
		expect(languageForPath('config.yaml')).toBe('yaml');
		expect(languageForPath('config.yml')).toBe('yaml');
		expect(languageForPath('guide.md')).toBe('markdown');
		expect(languageForPath('readme.markdown')).toBe('markdown');
		expect(languageForPath('page.html')).toBe('html');
		expect(languageForPath('style.css')).toBe('css');
		expect(languageForPath('run.sh')).toBe('shell');
		expect(languageForPath('query.sql')).toBe('sql');
		expect(languageForPath('Main.java')).toBe('java');
		expect(languageForPath('doc.xml')).toBe('xml');
	});

	it('is case-insensitive and resolves nested paths', () => {
		expect(languageForPath('SCRIPT.PY')).toBe('python');
		expect(languageForPath('docs/sub/config.YAML')).toBe('yaml');
	});

	it('falls back to plaintext for unknown or extensionless names', () => {
		expect(languageForPath('notes.txt')).toBe('');
		expect(languageForPath('image.png')).toBe('');
		expect(languageForPath('README')).toBe('');
		expect(languageForPath('')).toBe('');
	});
});

describe('buildTree', () => {
	it('returns an empty root for no paths', () => {
		const root = buildTree([]);
		expect(root.children).toEqual([]);
		expect(root.files).toEqual([]);
	});

	it('places a top-level file at the root', () => {
		const root = buildTree([{ id: 'a', path: 'guide.md' }]);
		expect(root.children).toEqual([]);
		expect(root.files).toHaveLength(1);
		expect(root.files[0].path).toBe('guide.md');
		expect(root.files[0].name).toBe('guide.md');
	});

	it('builds nested folders from a deep path', () => {
		const root = buildTree([{ id: 'a', path: 'docs/sub/guide.md' }]);
		expect(root.files).toEqual([]);
		expect(root.children).toHaveLength(1);

		const docs = root.children[0];
		expect(docs.name).toBe('docs');
		expect(docs.path).toBe('docs');
		expect(docs.children).toHaveLength(1);

		const sub = docs.children[0];
		expect(sub.name).toBe('sub');
		expect(sub.path).toBe('docs/sub');
		expect(sub.files).toHaveLength(1);
		expect(sub.files[0].name).toBe('guide.md');
		expect(sub.files[0].path).toBe('docs/sub/guide.md');
	});

	it('groups multiple files under the same folder', () => {
		const root = buildTree([
			{ id: 'a', path: 'docs/one.md' },
			{ id: 'b', path: 'docs/two.md' }
		]);
		expect(root.children).toHaveLength(1);
		const docs = root.children[0];
		expect(docs.files.map((f) => f.name).sort()).toEqual(['one.md', 'two.md']);
	});

	it('keeps sibling-prefix folders distinct (docs vs docs2)', () => {
		const root = buildTree([
			{ id: 'a', path: 'docs/one.md' },
			{ id: 'b', path: 'docs2/two.md' }
		]);
		const names = root.children.map((c) => c.name).sort();
		expect(names).toEqual(['docs', 'docs2']);
		const docs = root.children.find((c) => c.name === 'docs');
		const docs2 = root.children.find((c) => c.name === 'docs2');
		expect(docs?.files).toHaveLength(1);
		expect(docs2?.files).toHaveLength(1);
		expect(docs?.files[0].path).toBe('docs/one.md');
		expect(docs2?.files[0].path).toBe('docs2/two.md');
	});

	it('sorts folders before files and each alphabetically', () => {
		const root = buildTree([
			{ id: 'a', path: 'zeta.md' },
			{ id: 'b', path: 'alpha.md' },
			{ id: 'c', path: 'zfolder/x.md' },
			{ id: 'd', path: 'afolder/y.md' }
		]);
		expect(root.children.map((c) => c.name)).toEqual(['afolder', 'zfolder']);
		expect(root.files.map((f) => f.name)).toEqual(['alpha.md', 'zeta.md']);
	});

	it('merges a pending (empty) folder into the tree', () => {
		const root = buildTree([{ id: 'a', path: 'docs/one.md' }], ['drafts']);
		const names = root.children.map((c) => c.name).sort();
		expect(names).toEqual(['docs', 'drafts']);
		const drafts = root.children.find((c) => c.name === 'drafts');
		expect(drafts?.files).toEqual([]);
		expect(drafts?.pending).toBe(true);
	});

	it('does not duplicate a pending folder that already has files', () => {
		const root = buildTree([{ id: 'a', path: 'docs/one.md' }], ['docs']);
		const docs = root.children.filter((c) => c.name === 'docs');
		expect(docs).toHaveLength(1);
		// real folder wins; it is not marked pending
		expect(docs[0].pending).toBeFalsy();
		expect(docs[0].files).toHaveLength(1);
	});
});
