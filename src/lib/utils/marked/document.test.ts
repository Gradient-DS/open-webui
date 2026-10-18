import { describe, expect, it } from 'vitest';
import { Marked } from 'marked';
import extension from './extension';
import { markupSafeEnd, maskInFlightTag } from '../streamMarkup';
const marked = new Marked(extension());

describe('document blocks', () => {
	it.each(['', '</document>'])(
		'keeps the complete or streaming body in one card token: %s',
		(close) => {
			const raw = `<document title='Report' format='html'><style>p{color:red}</style><p>Secret body</p>${close}`;
			const tokens = marked.lexer(raw);
			expect(tokens).toHaveLength(1);
			expect(tokens[0]).toMatchObject({
				type: 'document',
				raw,
				document: { format: 'html', done: !!close }
			});
			expect(marked.parse(raw)).toBe('');
		}
	);
	it.each(['\n\n', '\n', ' '])('renders surrounding text with separator %j', (separator) => {
		const raw = `Before${separator}<document title="Report"># Hidden\n---\nBody</document>${separator}After`;
		const tokens = marked.lexer(raw);
		expect(tokens.filter((t) => t.type === 'document')).toHaveLength(1);
		expect(marked.parse(raw)).toContain('Before');
		expect(marked.parse(raw)).toContain('After');
		expect(marked.parse(raw)).not.toContain('Hidden');
	});
	it('never flashes markup or a document body at any stream boundary', () => {
		const raw =
			'Before\n<document title="A > B" format="markdown"># Secret\nBody [1]</document>\nAfter';
		for (let end = 1; end <= raw.length; end++) {
			const partial = maskInFlightTag(raw.slice(0, end));
			const html = marked.parse(partial.slice(0, markupSafeEnd(partial, partial.length)));
			expect(html).not.toMatch(/Secret|Body|document|format|title/);
		}
	});
	it('leaves the details tokenizer intact', () => {
		expect(
			marked.lexer('<details type="document">\n<summary>Document</summary>\n# Old\n</details>')[0]
		).toMatchObject({ type: 'details', text: '# Old' });
	});
});
