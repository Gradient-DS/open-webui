import { describe, expect, it } from 'vitest';
import { render } from 'svelte/server';
import Document from '$lib/components/icons/Document.svelte';
import MailSearch from '$lib/components/icons/MailSearch.svelte';
import OneDriveSearch from '$lib/components/icons/OneDriveSearch.svelte';
import SourceIcon from './SourceIcon.svelte';
import { sourceIcon } from './sourceIcon';
import { reduceSources } from './reduceSources';

describe('source icons', () => {
	it.each([
		['outlook_mail', MailSearch],
		['onedrive', OneDriveSearch]
	])('uses the composer icon for %s regardless of name or URL', (provider, component) => {
		for (const url of [undefined, 'https://unrelated.example/document']) {
			const source = { provider, name: 'A source', url };
			expect(sourceIcon(source)).toEqual({ component });
			const html = render(SourceIcon, { props: { source } }).body;
			expect(html).toContain('<svg');
			expect(html).not.toContain('<img');
		}
	});

	it.each(['https://outlook.office.com/mail/id/example', 'https://tenant.sharepoint.com/file'])(
		'keeps a web favicon for %s without inferring a provider',
		(url) => {
			for (const source of [{ name: url }, { name: 'OneDrive Outlook', url }]) {
				expect(sourceIcon(source)).toEqual({
					favicon: `https://www.google.com/s2/favicons?sz=32&domain=${encodeURIComponent(url)}`
				});
			}
		}
	);

	it('leaves missing and unknown providers on the ordinary fallback', () => {
		for (const provider of [undefined, 'unknown', 'toString']) {
			expect(sourceIcon({ provider, name: 'Outlook OneDrive.pdf' })).toEqual({
				component: Document
			});
		}
		const html = render(SourceIcon, {
			props: { source: { name: 'Web', url: 'https://example.com' } }
		}).body;
		expect(html).toContain('<img');
		expect(html).toContain('alt=""');
	});

	it.each(['outlook_mail', 'onedrive'])(
		'preserves %s through URL normalization, chunk merges and duplicate events',
		(provider) => {
			const base = {
				source: { id: 'https://example.com', name: 'Source' },
				document: ['First passage'],
				metadata: [{ chunk_id: 'first' }]
			};
			const withProvider = { ...base, source: { ...base.source, provider } };
			const result = reduceSources([
				base,
				withProvider,
				{ ...withProvider, document: ['Second passage'], metadata: [{ chunk_id: 'second' }] },
				base
			]);
			expect(result).toHaveLength(1);
			expect(result[0].document).toEqual(['First passage', 'Second passage']);
			expect(result[0].source.provider).toBe(provider);
			expect(sourceIcon(result[0].source)).toEqual(sourceIcon(withProvider.source));
			expect(base.source).not.toHaveProperty('provider');
		}
	);
});
