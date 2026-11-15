import { describe, expect, it, vi } from 'vitest';
import { render } from 'svelte/server';
import { readable } from 'svelte/store';
import { createInstance } from 'i18next';
import type { Connection, Schedule } from '$lib/apis/cloudSync';
import en from '$lib/i18n/locales/en-US/translation.json';
import nl from '$lib/i18n/locales/nl-NL/translation.json';
import SourceControls from './SourceControls.svelte';
import { SOURCE_UNAVAILABLE_MESSAGE } from './syncStatus';
import { removalTargets, sourceMenu } from './sources';

vi.mock('$lib/utils', () => ({ copyToClipboard: vi.fn() }));

const connection: Connection = { id: 'c', source_kind: 'onedrive', lifecycle: 'enabled' };
const source = (account: Connection): Schedule => ({
	id: 's',
	connection_id: account.id,
	connection: account,
	source_kind: account.source_kind,
	kind: 'content',
	lifecycle: 'suspended',
	last_error: 'access_revoked',
	scope: { include_descendants: true },
	subscribers: ['kb'],
	subscriber_count: 1,
	document_count: account.lifecycle === 'enabled' ? 0 : 5
});

describe.each([
	['en-US', en],
	['nl-NL', nl]
] as const)('source controls in %s', (language, translation) => {
	async function controls(account: Connection) {
		const i18n = createInstance();
		await i18n.init({
			lng: language,
			resources: { [language]: { translation } },
			returnEmptyString: false,
			keySeparator: false
		});
		const html = render(SourceControls, {
			props: { knowledgeId: 'kb', pair: { content: source(account) }, writeAccess: true },
			context: new Map([['i18n', readable(i18n)]])
		}).body;
		return { html, t: i18n.t.bind(i18n) };
	}

	it('explains unavailable sources and keeps removal available without reconnect', async () => {
		const { html, t } = await controls(connection);
		expect(html).toContain(t('Source unavailable'));
		expect(html).toContain(t(SOURCE_UNAVAILABLE_MESSAGE));
		expect(html).not.toContain(t('Reconnect'));
		expect(html).not.toContain(t('Sync now'));
		expect(html).toContain(t('Source actions'));
		const schedule = source(connection);
		expect(
			sourceMenu({
				itemPath: '',
				writeAccess: true,
				isAdmin: false,
				live: false,
				lifecycle: schedule.lifecycle
			})
		).toContain('remove');
		expect(removalTargets({ content: schedule })).toEqual([schedule]);
	});

	it.each(['reauth_required', 'refresh_rejected', 'access_revoked', null])(
		'offers reconnect and says files stay searchable for suspended:reauth with %s',
		async (last_error) => {
			const { html, t } = await controls({
				...connection,
				lifecycle: 'suspended:reauth',
				last_error
			});
			expect(html).toContain(t('Reconnect'));
			expect(html).toContain(
				t('Needs reconnect: your {{provider}} sign-in expired; synced files stay searchable', {
					provider: 'OneDrive'
				})
			);
			expect(html).not.toContain(t('Source unavailable'));
			expect(html).not.toContain(t(SOURCE_UNAVAILABLE_MESSAGE));
		}
	);
});
