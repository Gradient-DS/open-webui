import { describe, expect, it } from 'vitest';
import { render } from 'svelte/server';
import { readable } from 'svelte/store';
import CitationStackBody from './CitationStackBody.svelte';
import type { CitationDocument } from './citationDocuments';

describe('citation stack heading', () => {
	it.each([
		{ granularities: ['document'], label: 'Whole-document citation' },
		{ granularities: [undefined], label: '1 passages' },
		{ granularities: ['document', undefined], label: '2 passages' }
	])('labels $granularities citations', ({ granularities, label }) => {
		const documents: CitationDocument[] = granularities.map((granularity) => ({
			source: { name: 'Plan' },
			document: 'Read text',
			metadata: { granularity }
		}));
		const i18n = readable({
			t: (key: string, params?: { count?: number }) =>
				key.replace('{{count}}', String(params?.count ?? ''))
		});
		const html = render(CitationStackBody, {
			props: {
				citation: {
					id: 'plan',
					source: { name: 'Plan' },
					document: documents.map((document) => document.document),
					metadata: documents.map((document) => document.metadata ?? {}),
					distances: documents.map(() => undefined)
				},
				mergedDocuments: documents,
				previewAvailable: false
			},
			context: new Map([['i18n', i18n]])
		}).body;
		expect(html).toContain(label);
		if (label === 'Whole-document citation') expect(html).not.toContain('1 passages');
	});
});
