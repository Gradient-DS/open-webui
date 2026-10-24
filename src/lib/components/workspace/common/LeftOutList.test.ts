import { describe, expect, it } from 'vitest';
import { render } from 'svelte/server';
import { writable } from 'svelte/store';
import type { i18n } from 'i18next';
import LeftOutList from './LeftOutList.svelte';

const context = new Map([['i18n', writable({ t: (key: string) => key } as i18n)]]);

describe('items left out of a folder or assistant share', () => {
	it('does not show an empty warning', () => {
		expect(render(LeftOutList, { context, props: { items: [] } }).body).not.toContain('<ul');
	});
	it('names each item and explains why it stays private', () => {
		const html = render(LeftOutList, {
			context,
			props: {
				items: [
					{ type: 'collection', id: 'cloud', name: 'Fable projects', reason: 'cloud' },
					{ type: 'note', id: 'note', name: 'Fern’s notes', reason: 'owner' }
				]
			}
		}).body;
		expect(html).toContain('Fable projects');
		expect(html).toContain('Fern’s notes');
		expect(html).toContain('synced from the cloud, never shared');
		expect(html).toContain("someone else's, only its owner can share it");
	});
});
