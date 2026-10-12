import { describe, expect, it } from 'vitest';
import { render } from 'svelte/server';
import { readable } from 'svelte/store';
import CitationSourceList from './CitationSourceList.svelte';

describe('citation source list row', () => {
	it.each([
		{ granularities: ['document'], label: 'Whole-document citation' },
		{ granularities: [undefined], label: '1 passages' },
		{ granularities: ['document', undefined], label: '2 passages' }
	])('labels $granularities citations', ({ granularities, label }) => {
		const i18n = readable({
			t: (key: string, params?: { count?: number; name?: string }) =>
				key
					.replace('{{count}}', String(params?.count ?? ''))
					.replace('{{name}}', params?.name ?? '')
		});
		const html = render(CitationSourceList, {
			props: {
				visibleCitations: [
					{
						id: 'plan',
						source: { name: 'Plan' },
						document: granularities.map((_, i) => `Read text ${i}`),
						metadata: granularities.map((granularity) => ({ granularity })),
						distances: granularities.map(() => undefined)
					}
				],
				onSelect: () => {}
			},
			context: new Map([['i18n', i18n]])
		}).body;
		expect(html).toContain(label);
		if (label === 'Whole-document citation') expect(html).not.toContain('1 passages');
	});
});
