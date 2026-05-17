import { describe, it, expect } from 'vitest';
import { reduceSources, type RawSource, type DisplayCitation } from './reduceSources';

const sourceA = (chunkText: string, page: number, fileId = 'doc-A'): RawSource => ({
	source: { name: 'A.pdf', type: 'file', id: 'doc-A' },
	document: [chunkText],
	metadata: [{ source: 'A.pdf', file_id: fileId, page, name: 'A.pdf' }],
	distances: [0.9]
});

describe('reduceSources', () => {
	it('returns one entry per unique source', () => {
		const out = reduceSources([sourceA('chunk1', 0)]);
		expect(out).toHaveLength(1);
		expect(out[0].source.name).toBe('A.pdf');
		expect(out[0].document).toEqual(['chunk1']);
		expect(out[0].metadata).toHaveLength(1);
		expect(out[0].distances).toEqual([0.9]);
	});

	it('skips empty source objects', () => {
		const out = reduceSources([{} as RawSource, sourceA('chunk1', 0)]);
		expect(out).toHaveLength(1);
		expect(out[0].document).toEqual(['chunk1']);
	});

	it('merges two source events for the same doc into one entry', () => {
		const out = reduceSources([sourceA('chunk1', 0), sourceA('chunk2', 1)]);
		expect(out).toHaveLength(1);
		expect(out[0].document).toEqual(['chunk1', 'chunk2']);
		expect(out[0].metadata).toHaveLength(2);
	});

	it('dedupes when the exact same chunk arrives twice (the staging-feedback bug)', () => {
		const out = reduceSources([sourceA('chunk1', 0), sourceA('chunk1', 0)]);
		expect(out).toHaveLength(1);
		expect(out[0].document).toEqual(['chunk1']);
		expect(out[0].metadata).toHaveLength(1);
		expect(out[0].distances).toEqual([0.9]);
	});

	it('treats whitespace-only differences as duplicates', () => {
		const out = reduceSources([
			sourceA('The   quick brown fox', 0),
			sourceA('The quick brown fox\n', 0)
		]);
		expect(out).toHaveLength(1);
		expect(out[0].document).toHaveLength(1);
	});

	it('keeps two chunks on the same page when content differs', () => {
		const out = reduceSources([sourceA('first half', 0), sourceA('second half', 0)]);
		expect(out).toHaveLength(1);
		expect(out[0].document).toEqual(['first half', 'second half']);
	});

	it('keeps chunks of identical text when chunk_id distinguishes them', () => {
		const ev = (text: string, chunkId: string): RawSource => ({
			source: { name: 'A.pdf', type: 'file', id: 'doc-A' },
			document: [text],
			metadata: [{ source: 'A.pdf', file_id: 'doc-A', page: 0, chunk_id: chunkId }],
			distances: [0.5]
		});
		const out = reduceSources([ev('boilerplate', 'c1'), ev('boilerplate', 'c2')]);
		expect(out).toHaveLength(1);
		expect(out[0].document).toEqual(['boilerplate', 'boilerplate']);
		expect(out[0].metadata).toHaveLength(2);
	});

	it('groups by source-document id when metadata.source is missing', () => {
		const ev = (text: string): RawSource => ({
			source: { id: 'doc-X', name: 'X.pdf' },
			document: [text],
			metadata: [{ file_id: 'doc-X', page: 0 }],
			distances: [0.5]
		});
		const out = reduceSources([ev('alpha'), ev('beta')]);
		expect(out).toHaveLength(1);
		expect(out[0].document).toEqual(['alpha', 'beta']);
	});

	it('treats different documents as separate entries', () => {
		const sourceB: RawSource = {
			source: { name: 'B.pdf', type: 'file', id: 'doc-B' },
			document: ['chunkX'],
			metadata: [{ source: 'B.pdf', file_id: 'doc-B', page: 0 }],
			distances: [0.7]
		};
		const out = reduceSources([sourceA('chunk1', 0), sourceB]);
		expect(out).toHaveLength(2);
		const names = out.map((c: DisplayCitation) => c.source.name);
		expect(names).toEqual(['A.pdf', 'B.pdf']);
	});

	it('promotes metadata.name onto the merged source.name when present', () => {
		const ev: RawSource = {
			source: { type: 'file', id: 'doc-A' },
			document: ['chunk1'],
			metadata: [{ source: 'A.pdf', file_id: 'doc-A', name: 'Pretty Name', page: 0 }],
			distances: [0.5]
		};
		const out = reduceSources([ev]);
		expect(out[0].source.name).toBe('Pretty Name');
	});

	it('rewrites URL-style ids to a self-named link source', () => {
		const ev: RawSource = {
			source: { type: 'file' },
			document: ['chunk1'],
			metadata: [{ source: 'https://example.com/page', page: 0 }],
			distances: [0.5]
		};
		const out = reduceSources([ev]);
		expect(out[0].source.name).toBe('https://example.com/page');
		expect(out[0].source.url).toBe('https://example.com/page');
	});
});
