import { describe, expect, it } from 'vitest';
import { Marked } from 'marked';
import markedExtension from './marked/extension';
import { encode } from 'html-entities';
import { extractDocumentsFromMessage } from './agentDocument';

const marker = (format: string, content: string, title = 'Report') =>
	`<details type="document" format="${format}" title="${encode(title)}" done="true"><summary>Document</summary>\n${encode(content)}\n</details>`;

describe('agent documents', () => {
	it.each(['html', 'markdown'])('unescapes %s content and title exactly once', (format) => {
		const content = `  <p title="A & B">'quoted' &lt;literal&gt;</p>  \n`;
		const title = `<Report> & "quotes" 'apostrophe'`;
		expect(extractDocumentsFromMessage(marker(format, content, title))).toEqual([
			{ title, content, format, isAgentDocument: true }
		]);
	});
	it('preserves raw legacy markdown and tool arguments in message order', () => {
		const args = encode(JSON.stringify({ title: 'Old tool', markdown: '# Tool &amp;' }));
		const content = `<details type="document" title="Old &amp; raw"><summary>Document</summary>\n# Raw &amp; <b>bold</b>\n</details>`;
		expect(
			extractDocumentsFromMessage(
				`<details type="tool_calls" name="write_document" arguments="${args}"></details>${content}`
			)
		).toEqual([
			{ title: 'Old tool', content: '# Tool &amp;', format: 'markdown', isAgentDocument: false },
			{
				title: 'Old & raw',
				content: '# Raw &amp; <b>bold</b>',
				format: 'markdown',
				isAgentDocument: false
			}
		]);
	});
	it('extracts several documents in order and ignores unrelated details', () => {
		expect(
			extractDocumentsFromMessage(
				marker('html', '<p>First</p>') +
					'<details type="reasoning">Thinking</details>' +
					marker('markdown', '# Second')
			).map((doc) => doc.content)
		).toEqual(['<p>First</p>', '# Second']);
	});
	it('ignores a half-streamed marker', () => {
		expect(
			extractDocumentsFromMessage(
				marker('html', '<p>First</p>') + marker('markdown', '# Second').replace('</details>', '')
			)
		).toHaveLength(1);
	});
});

it('tokenizes the exact backend marker as a document card without inline HTML', () => {
	const marked = new Marked(markedExtension());
	const tokens = marked.lexer(marker('html', '<style>p { color: red }</style><p>Content</p>'));
	expect(tokens[0]).toMatchObject({
		type: 'details',
		attributes: { type: 'document', format: 'html' },
		summary: 'Document'
	});
	expect(tokens).toHaveLength(1);
});
