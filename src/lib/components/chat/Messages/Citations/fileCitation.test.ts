import { describe, expect, it } from 'vitest';
import { fileCitation, opensInPanel } from './fileCitation';

describe('opensInPanel', () => {
	it('opens stored chat files, processing ones included', () => {
		expect(opensInPanel({ type: 'file', id: 'f1', status: 'uploaded' })).toBe(true);
		expect(opensInPanel({ type: 'file', id: 'f1', status: 'processing' })).toBe(true);
	});

	it('keeps the modal for uploads, collections and missing items', () => {
		expect(opensInPanel({ type: 'file', id: 'f1', status: 'uploading' })).toBe(false);
		expect(opensInPanel({ type: 'file', id: null })).toBe(false);
		expect(opensInPanel({ type: 'collection', id: 'kb1' })).toBe(false);
		expect(opensInPanel(null)).toBe(false);
	});
});

describe('fileCitation', () => {
	it('builds a whole-document source keyed by the file id', () => {
		const citation = fileCitation({ type: 'file', id: 'f1', name: 'Handbook.docx' });
		expect(citation.id).toBe('f1');
		expect(citation.source).toEqual({ id: 'f1', name: 'Handbook.docx', type: 'file' });
		expect(citation.document).toEqual(['']);
		expect(citation.metadata).toEqual([
			{ file_id: 'f1', name: 'Handbook.docx', granularity: 'document' }
		]);
	});

	it('keeps the provider, an https original link and inline text', () => {
		const citation = fileCitation({
			type: 'file',
			id: 'f2',
			name: 'Plan.pdf',
			source: { provider: 'onedrive', ref: 'r' },
			web_url: 'https://tenant.example/plan.pdf',
			file: { data: { content: 'Plan text' } }
		});
		expect(citation.source.provider).toBe('onedrive');
		expect(citation.metadata[0].source_url).toBe('https://tenant.example/plan.pdf');
		expect(citation.document).toEqual(['Plan text']);
	});

	it('drops a non-https original link', () => {
		const citation = fileCitation({ type: 'file', id: 'f3', web_url: 'http://insecure.example' });
		expect(citation.metadata[0].source_url).toBeUndefined();
	});
});
