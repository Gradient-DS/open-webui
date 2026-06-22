import { describe, it, expect } from 'vitest';
import { buildTree, isMarkdownPath, filterMarkdownFiles } from './utils';

describe('isMarkdownPath', () => {
	it('accepts .md (case-insensitive)', () => {
		expect(isMarkdownPath('guide.md')).toBe(true);
		expect(isMarkdownPath('GUIDE.MD')).toBe(true);
		expect(isMarkdownPath('docs/sub/guide.md')).toBe(true);
	});

	it('accepts .markdown (case-insensitive)', () => {
		expect(isMarkdownPath('readme.markdown')).toBe(true);
		expect(isMarkdownPath('README.MARKDOWN')).toBe(true);
	});

	it('rejects non-markdown extensions', () => {
		expect(isMarkdownPath('image.png')).toBe(false);
		expect(isMarkdownPath('notes.txt')).toBe(false);
		expect(isMarkdownPath('archive.md.zip')).toBe(false);
		expect(isMarkdownPath('script.js')).toBe(false);
	});

	it('rejects names with no extension or empty', () => {
		expect(isMarkdownPath('README')).toBe(false);
		expect(isMarkdownPath('')).toBe(false);
		expect(isMarkdownPath('md')).toBe(false);
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

describe('filterMarkdownFiles', () => {
	const f = (name: string) => ({ name }) as File;

	it('keeps only markdown files and counts the skipped rest', () => {
		const files = [f('a.md'), f('b.png'), f('c.markdown'), f('d.txt')];
		const { kept, skipped } = filterMarkdownFiles(files);
		expect(kept.map((x) => x.name)).toEqual(['a.md', 'c.markdown']);
		expect(skipped).toBe(2);
	});

	it('keeps all when every file is markdown', () => {
		const files = [f('a.md'), f('b.markdown')];
		const { kept, skipped } = filterMarkdownFiles(files);
		expect(kept).toHaveLength(2);
		expect(skipped).toBe(0);
	});

	it('skips all when none are markdown', () => {
		const files = [f('a.png'), f('b.txt')];
		const { kept, skipped } = filterMarkdownFiles(files);
		expect(kept).toHaveLength(0);
		expect(skipped).toBe(2);
	});

	it('handles an empty list', () => {
		const { kept, skipped } = filterMarkdownFiles([]);
		expect(kept).toHaveLength(0);
		expect(skipped).toBe(0);
	});
});
