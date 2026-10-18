import { describe, it, expect } from 'vitest';
import { isDocumentUrl, routeWebAttachment } from './webAttachments';

describe('isDocumentUrl', () => {
	it('matches document extensions the agent fetcher cannot read', () => {
		for (const ext of ['pdf', 'docx', 'xlsx', 'pptx', 'csv', 'epub']) {
			expect(isDocumentUrl(`https://example.com/report.${ext}`)).toBe(true);
		}
	});

	it('ignores case and query strings', () => {
		expect(isDocumentUrl('https://example.com/Report.PDF?download=1&v=2')).toBe(true);
	});

	it('does not match ordinary pages', () => {
		expect(isDocumentUrl('https://example.com/')).toBe(false);
		expect(isDocumentUrl('https://example.com/blog/some-post')).toBe(false);
		expect(isDocumentUrl('https://example.com/index.html')).toBe(false);
		expect(isDocumentUrl('https://example.com/a.b/page')).toBe(false);
	});

	it('does not match a dotfile-style segment with no extension', () => {
		expect(isDocumentUrl('https://example.com/.pdf')).toBe(false);
	});

	it('is false for anything unparseable', () => {
		expect(isDocumentUrl('not a url')).toBe(false);
		expect(isDocumentUrl('')).toBe(false);
	});
});

describe('routeWebAttachment', () => {
	it('always ingests when the chat is not agent-routed', () => {
		// Upstream behaviour, untouched on non-agent deployments.
		expect(routeWebAttachment('https://example.com/article', false)).toBe('ingest');
		expect(routeWebAttachment('https://www.youtube.com/watch?v=abc', false)).toBe('ingest');
	});

	it('hands a plain web page to the agent', () => {
		expect(routeWebAttachment('https://example.com/article', true)).toBe('agent');
	});

	it('keeps YouTube on the ingest path', () => {
		// /process/youtube pulls the transcript; the agent's fetcher would only
		// see the watch page's HTML.
		for (const url of [
			'https://www.youtube.com/watch?v=abc',
			'https://youtu.be/abc',
			'https://youtube.com/watch?v=abc',
			'https://m.youtube.com/watch?v=abc'
		]) {
			expect(routeWebAttachment(url, true)).toBe('ingest');
		}
	});

	it('keeps document URLs on the ingest path', () => {
		// get_content_from_url downloads these and runs the document loader.
		expect(routeWebAttachment('https://example.com/spec.pdf', true)).toBe('ingest');
		expect(routeWebAttachment('https://example.com/sheet.xlsx', true)).toBe('ingest');
	});

	it('sends an extension-less document URL to the agent', () => {
		// Known limitation: the real signal is the response Content-Type, which
		// the client cannot see. The agent reports it could not read the page.
		expect(routeWebAttachment('https://example.com/download?id=123', true)).toBe('agent');
	});
});
