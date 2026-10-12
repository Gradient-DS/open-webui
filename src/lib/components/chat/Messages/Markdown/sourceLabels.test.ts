import { describe, expect, it } from 'vitest';
import { chipLabel, sourceLabels } from './sourceLabels';
import { reduceSources, type RawSource } from '../Citations/reduceSources';

const source = (ref: string, name: string, n: number, chunk = `${ref}#0`): RawSource => ({
	source: { id: ref, name, url: ref },
	document: [`text of ${chunk}`],
	metadata: [{ source: ref, name, chunk_id: chunk }],
	n
});

describe('sourceLabels', () => {
	it('gives two documents with the same name their own number', () => {
		const sources = [
			source('deck', 'Quickscan.pptx', 1),
			source('mail-a', 'NDA - N2000-gebied Stein', 2),
			source('mail-b', 'NDA - N2000-gebied Stein', 3),
			source('onedrive/1', 'Sjabloon.docx', 4)
		];
		const labels = sourceLabels(sources);
		expect(labels).toEqual([
			'Quickscan.pptx',
			'NDA - N2000-gebied Stein',
			'NDA - N2000-gebied Stein',
			'Sjabloon.docx'
		]);
		expect(labels).toHaveLength(reduceSources(sources).length);
	});

	it('gives the chunks of one document one number', () => {
		const sources = [
			source('deck', 'Quickscan.pptx', 1, 'a'),
			source('deck', 'Quickscan.pptx', 1, 'b')
		];
		expect(sourceLabels(sources)).toEqual(['Quickscan.pptx']);
	});

	it('labels a web page by its title, else by its url', () => {
		const titled = source('https://example.org/a', 'Example', 1);
		const bare: RawSource = {
			source: { id: 'https://example.org/b' },
			document: ['b'],
			metadata: [{ source: 'https://example.org/b' }]
		};
		expect(sourceLabels([titled, bare])).toEqual(['Example', 'https://example.org/b']);
	});

	it('labels each marker by the stored number, not the arrival order', () => {
		expect(sourceLabels([source('deck', 'Quickscan.pptx', 2), source('note', 'Note', 1)])).toEqual([
			'Note',
			'Quickscan.pptx'
		]);
	});

	it('labels every source N/A when citations are off', () => {
		expect(sourceLabels([source('a', 'A', 1), source('b', 'B', 2)], true)).toEqual(['N/A', 'N/A']);
	});
});

describe('chipLabel', () => {
	const labels = ['Quickscan.pptx', 'NDA', 'Sjabloon.docx'];

	it('names the first cited source and counts the rest', () => {
		expect(chipLabel([2, 3], labels)).toEqual({ title: 'NDA', more: 1 });
	});

	it('leaves out a marker past the end of the sources', () => {
		expect(chipLabel([3, 4], labels)).toEqual({ title: 'Sjabloon.docx', more: 0 });
		expect(chipLabel([4, 2], labels)).toEqual({ title: 'NDA', more: 0 });
	});

	it('names nothing when no marker has a source', () => {
		expect(chipLabel([4, 5], labels)).toBeNull();
	});
});
