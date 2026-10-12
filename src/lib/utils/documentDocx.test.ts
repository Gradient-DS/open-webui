// @vitest-environment jsdom
import { describe, expect, it } from 'vitest';
import JSZip from 'jszip';
import { buildDocumentDocx } from './documentDocx';

const sources = [
	{ source: { id: 'a', name: 'Verslag.pdf' }, document: ['x'], metadata: [{}] },
	{
		source: { id: 'b', name: 'Wet', url: 'https://wetten.overheid.nl/x' },
		document: ['y'],
		metadata: [{}]
	}
];

async function unzip(blob: Blob) {
	const zip = await JSZip.loadAsync(blob);
	const read = (name: string) => zip.file(name)?.async('string') ?? Promise.resolve('');
	return {
		body: await read('word/document.xml'),
		notes: await read('word/footnotes.xml'),
		footer: await read('word/footer1.xml')
	};
}

describe('browser Word export', () => {
	it('turns citations into footnotes and keeps the document structure', async () => {
		const { body, notes, footer } = await unzip(
			await buildDocumentDocx(
				'Memo',
				'Intro & claim [2] and [1, 2].\n\n## Kop\n\n| A | B |\n|---|---|\n| 1 | 2 |\n\n1. een\n2. twee',
				sources,
				'Bronnen'
			)
		);
		expect(body).toContain('Memo');
		expect(body).toContain('Intro &amp; claim');
		expect(body).not.toContain('[2]');
		expect(body.match(/w:footnoteReference /g)).toHaveLength(3);
		expect(notes).toContain('Verslag.pdf');
		expect(notes).toContain('Wet — https://wetten.overheid.nl/x');
		expect(body).toContain('<w:tbl>');
		expect(body).toContain('w:numPr');
		expect(footer).toContain('PAGE');
	});

	it('drops remote images and unsafe links instead of referencing them', async () => {
		const { body } = await unzip(
			await buildDocumentDocx(
				'Memo',
				'![kaart](https://evil.test/a.png) [lokaal](file:///etc/passwd) [site](https://soev.ai)',
				[],
				'Bronnen'
			)
		);
		expect(body).not.toContain('evil.test');
		expect(body).not.toContain('file:');
		expect(body).toContain('kaart');
		expect(body).toContain('lokaal');
	});

	it('lists uncited sources at the end', async () => {
		const { body } = await unzip(
			await buildDocumentDocx('Memo', 'Geen markers.', sources, 'Bronnen')
		);
		expect(body).toContain('Bronnen');
		expect(body).toContain('[1] Verslag.pdf');
	});
});
