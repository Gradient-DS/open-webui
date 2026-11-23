import { describe, expect, it } from 'vitest';
import { render } from 'svelte/server';
import { marked } from 'marked';
import MarkdownInlineTokens from '$lib/components/chat/Messages/Markdown/MarkdownInlineTokens.svelte';
import { safeHref } from './safeHref';

describe('safeHref', () => {
	it('keeps web, mail and relative links', () => {
		for (const href of [
			'https://example.com/a?b=c#d',
			'http://example.com',
			'mailto:info@example.com',
			'/c/123',
			'/notes/abc',
			'#footnote-1',
			'relative/path',
			'?q=1'
		]) {
			expect(safeHref(href)).toBe(href);
		}
	});

	it('drops schemes that run or embed content, however they are spelled', () => {
		for (const href of [
			'javascript:alert(1)',
			'JaVaScRiPt:alert(1)',
			' javascript:alert(1)',
			'\u0001javascript:alert(1)',
			'java\tscript:alert(1)',
			'java\nscript:alert(1)',
			'data:text/html,<script>alert(1)</script>',
			'vbscript:msgbox(1)',
			'file:///etc/passwd',
			'blob:https://example.com/x',
			'',
			'   ',
			undefined,
			null
		]) {
			expect(safeHref(href)).toBeNull();
		}
	});
});

describe('markdown links', () => {
	const inline = (markdown: string) => {
		const [paragraph] = marked.lexer(markdown) as unknown as { tokens: never[] }[];
		return render(MarkdownInlineTokens, {
			props: { id: 't', tokens: paragraph.tokens },
			context: new Map([['i18n', { subscribe: () => () => {} }]])
		}).body;
	};

	it('renders a javascript: link as its text only', () => {
		const html = inline('Zie [het besluit](javascript:alert(document.domain)) hier');
		expect(html).toContain('het besluit');
		expect(html).not.toContain('<a');
		expect(html).not.toContain('javascript:');
	});

	it('renders a data: autolink as text, and a web link as a link', () => {
		expect(inline('<data:text/html,x>')).not.toContain('<a');
		expect(inline('[site](https://example.com)')).toContain('href="https://example.com"');
	});
});
