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
		footer: await read('word/footer1.xml')
	};
}

describe('browser Word export', () => {
	it('keeps [n] markers, lists each source once and keeps the document structure', async () => {
		const { body, footer } = await unzip(
			await buildDocumentDocx(
				'Memo',
				'Intro & claim [2] and [1, 2].\n\n## Kop\n\n| A | B |\n|---|---|\n| 1 | 2 |\n\n1. een\n2. twee',
				sources,
				'Bronnen'
			)
		);
		expect(body).toContain('Memo');
		expect(body).toContain('Intro &amp; claim');
		// Renumbered by first use: source b is [1], source a is [2].
		expect(body).toContain('claim [1] and [2, 1].');
		expect(body).not.toContain('w:footnoteReference');
		expect(body.match(/\[1\] Wet — https:\/\/wetten.overheid.nl\/x/g)).toHaveLength(1);
		expect(body.match(/\[2\] Verslag.pdf/g)).toHaveLength(1);
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

	it('uses the opening heading as the title when the document starts with a newline', async () => {
		const { body } = await unzip(
			await buildDocumentDocx('Memo', '\n# Eigen titel\n\nTekst', [], 'Bronnen')
		);
		const firstParagraph = body.match(/<w:body><w:p>[\s\S]*?<\/w:p>/)?.[0] ?? '';
		expect(firstParagraph).toContain('w:val="Title"');
		expect(firstParagraph).toContain('Eigen titel');
	});

	it('lists uncited sources at the end', async () => {
		const { body } = await unzip(
			await buildDocumentDocx('Memo', 'Geen markers.', sources, 'Bronnen')
		);
		expect(body).toContain('Bronnen');
		expect(body).toContain('[1] Verslag.pdf');
	});
});
