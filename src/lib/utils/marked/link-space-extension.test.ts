import { describe, expect, it } from 'vitest';
import { Marked } from 'marked';

import { linkSpaceExtension } from './link-space-extension';

const render = (src: string) => {
	const marked = new Marked();
	marked.use(linkSpaceExtension);
	return marked.parse(src) as string;
};

const SHAREPOINT =
	'https://contoso-my.sharepoint.com/personal/lex/Documents/5. Projects/7. NEO NL/260303%20-%20Eindpresentatie.pptx';

describe('link space extension', () => {
	it('links to an address with spaces, encoding them', () => {
		expect(render(`Zie [de presentatie](${SHAREPOINT}).`)).toBe(
			'<p>Zie <a href="https://contoso-my.sharepoint.com/personal/lex/Documents/5.%20Projects/7.%20NEO%20NL/260303%20-%20Eindpresentatie.pptx">de presentatie</a>.</p>\n'
		);
	});

	it('keeps parentheses in a file name', () => {
		expect(render('[x](https://contoso.sharepoint.com/Shared Documents/Plan (v2).docx)')).toBe(
			'<p><a href="https://contoso.sharepoint.com/Shared%20Documents/Plan%20(v2).docx">x</a></p>\n'
		);
	});

	it('renders the link text as markdown', () => {
		expect(render('[**deck**](https://contoso.sharepoint.com/a b.pptx)')).toBe(
			'<p><a href="https://contoso.sharepoint.com/a%20b.pptx"><strong>deck</strong></a></p>\n'
		);
	});

	it('leaves ordinary links and links with a title to marked', () => {
		expect(render('[x](https://example.com/a)')).toBe(
			'<p><a href="https://example.com/a">x</a></p>\n'
		);
		expect(render('[x](https://example.com/a "Title")')).toBe(
			'<p><a href="https://example.com/a" title="Title">x</a></p>\n'
		);
	});

	it('does not touch links in code', () => {
		expect(render('`[x](https://contoso.sharepoint.com/a b)`')).toBe(
			'<p><code>[x](https://contoso.sharepoint.com/a b)</code></p>\n'
		);
	});
});
