import { describe, expect, it } from 'vitest';
import { encode } from 'html-entities';
import { extractDocumentsFromMessage } from './agentDocument';

const marker = (format: string, content: string, title = 'Report') =>
	`<document title="${encode(title)}" format="${format}">${content}</document>`;

describe('streamed documents', () => {
	it.each(['html', 'markdown'])('keeps raw %s content and decodes the title once', (format) => {
		const content = `  <p title="A & B">&lt;literal&gt;</p>  \n`;
		const title = `<Report> & "quotes" 'apostrophe'`;
		expect(extractDocumentsFromMessage(marker(format, content, title))).toEqual([
			{ title, content, format, done: true }
		]);
	});
	it('accepts single quotes and a greater-than sign in a title', () => {
		expect(
			extractDocumentsFromMessage(
				"<document title='A > B &amp; C' format='html'><p>x</p></document>"
			)[0]
		).toMatchObject({ title: 'A > B & C', format: 'html' });
	});
	it('returns an empty or growing body as an in-progress document', () => {
		for (const body of ['', '# Draft', '<h1>Draft']) {
			expect(extractDocumentsFromMessage(`<document title="Draft">${body}`)[0]).toMatchObject({
				content: body,
				done: false
			});
		}
		expect(extractDocumentsFromMessage('<document title="Draft')).toEqual([]);
	});
	it('preserves raw legacy markdown and tool arguments', () => {
		const args = encode(JSON.stringify({ title: 'Old tool', markdown: '# Tool &amp;' }));
		expect(
			extractDocumentsFromMessage(
				`<details type="tool_calls" name="write_document" done="true" arguments="${args}"></details><details type="document" title="Old &amp; raw"><summary>Document</summary>\n# Raw &amp; <b>bold</b>\n</details>`
			)
		).toEqual([
			{ title: 'Old tool', content: '# Tool &amp;', format: 'markdown', done: true },
			{ title: 'Old & raw', content: '# Raw &amp; <b>bold</b>', format: 'markdown', done: true }
		]);
	});
	it('extracts several documents and does not parse details inside their bodies', () => {
		const html = '<details type="document">Nested</details>';
		expect(
			extractDocumentsFromMessage(marker('html', html) + 'after\n<document title="Next"># Second')
		).toEqual([
			{ title: 'Report', content: html, format: 'html', done: true },
			{ title: 'Next', content: '# Second', format: 'markdown', done: false }
		]);
	});
	it('treats a closing tag inside a markdown fence as the end too', () => {
		expect(
			extractDocumentsFromMessage(marker('markdown', '```\n</document>\n```'))[0].content
		).toBe('```\n');
	});
});
