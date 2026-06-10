import { describe, it, expect } from 'vitest';
import { citationExtension } from './citation-extension';

const tokenize = (src: string) => citationExtension().tokenizer(src);

describe('citation tokenizer — duplicate ids within a run', () => {
	it('collapses repeated fullwidth citations of the same source', () => {
		// ChatGPT-style per-chunk metadata markers: same source, different
		// line ranges. The metadata is dropped, so the duplicates must
		// collapse to a single chip instead of a "+1 more sources" group.
		const token = tokenize('【4†L1-L4】【4†L83-L86】');
		expect(token?.ids).toEqual([4]);
		expect(token?.citationIdentifiers).toEqual(['4']);
	});

	it('collapses repeated ids in standard bracket groups', () => {
		const token = tokenize('[4, 4]');
		expect(token?.ids).toEqual([4]);
	});

	it('keeps distinct ids in a run', () => {
		const token = tokenize('[1][2]');
		expect(token?.ids).toEqual([1, 2]);
	});

	it('keeps distinct #suffix anchors on the same id', () => {
		const token = tokenize('[4#intro][4#conclusion]');
		expect(token?.ids).toEqual([4, 4]);
		expect(token?.citationIdentifiers).toEqual(['4#intro', '4#conclusion']);
	});

	it('mixed forms dedupe across adjacent blocks', () => {
		const token = tokenize('[3]【3†L1-L2】');
		expect(token?.ids).toEqual([3]);
	});
});
