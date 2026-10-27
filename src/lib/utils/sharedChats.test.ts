import { describe, expect, it } from 'vitest';

import sidebarSource from '$lib/components/layout/Sidebar.svelte?raw';
import sectionSource from '$lib/components/layout/Sidebar/Section.svelte?raw';
import searchSource from '$lib/components/layout/SearchModal.svelte?raw';
import en from '$lib/i18n/locales/en-US/translation.json';
import nl from '$lib/i18n/locales/nl-NL/translation.json';
import { isSharedChat, splitChatsByOwnership } from './sharedChats';

const row = (id: string, owned?: boolean) => ({ id, title: `Fictional ${id}`, owned });

describe('splitChatsByOwnership', () => {
	it('keeps own chats in the list and moves shared ones to their own section', () => {
		const own = row('own-1', true);
		const shared = row('shared-1', false);
		const legacy = row('legacy-1');
		expect(splitChatsByOwnership([own, shared, legacy], [])).toEqual({
			own: [own, legacy],
			pinned: [],
			shared: [shared]
		});
	});

	it('keeps pinned for own chats and never drops a pinned shared chat', () => {
		const pinnedOwn = row('pinned-own', true);
		const pinnedShared = row('pinned-shared', false);
		const shared = row('shared-1', false);
		const split = splitChatsByOwnership([shared], [pinnedOwn, pinnedShared]);
		expect(split.pinned).toEqual([pinnedOwn]);
		expect(split.shared).toEqual([pinnedShared, shared]);
	});

	it('lists a shared chat once when it is in both lists', () => {
		const shared = row('shared-1', false);
		expect(splitChatsByOwnership([shared], [shared]).shared).toEqual([shared]);
	});

	it('keeps the loading state of the chat list', () => {
		expect(splitChatsByOwnership(null, [row('shared-1', false)])).toEqual({
			own: null,
			pinned: [],
			shared: [row('shared-1', false)]
		});
	});

	it('treats only an explicit platform owned=false as shared', () => {
		expect(isSharedChat(row('a', false))).toBe(true);
		expect(isSharedChat(row('b', true))).toBe(false);
		expect(isSharedChat(row('c'))).toBe(false);
	});
});

describe('Shared with me section', () => {
	const section = sidebarSource.match(
		/\{#if chatLists\.shared\.length > 0\}([\s\S]*?)<\/SidebarSection>/
	)?.[1];

	it('is collapsed by default and remembers its state like the other sections', () => {
		expect(sidebarSource).toMatch(/\blet showSharedChats = false;/);
		expect(section).toContain('id="sidebar-shared-chats"');
		expect(section).toContain('bind:open={showSharedChats}');
		expect(sectionSource).toContain('localStorage.getItem(`${id}-folder-state`)');
		expect(sectionSource).toContain('localStorage.setItem(`${id}-folder-state`, `${state}`)');
	});

	it('renders shared chats read-only without owner actions', () => {
		expect(section).toContain("name={$i18n.t('Shared with me')}");
		expect(section).toContain('{#each chatLists.shared as chat');
		expect(section).toContain('readonly={true}');
		expect(section).not.toMatch(/on:change|on:tag|onReadStateChange|on:drop|on:import/);
	});

	it('feeds the chat and pinned lists only with own chats', () => {
		expect(sidebarSource).toContain('$: chatLists = splitChatsByOwnership($chats, $pinnedChats);');
		expect(sidebarSource).not.toMatch(/\{#each \$chats\b|\{#each \$pinnedChats\b/);
		expect(sidebarSource).toContain('{#each chatLists.own as chat');
		expect(sidebarSource).toContain('{#each chatLists.pinned as chat');
	});

	it('marks shared chats in search', () => {
		expect(searchSource).toMatch(/\{#if isSharedChat\(chat\)\}[\s\S]*?\$i18n\.t\('Shared'\)/);
	});

	it('has English keys and Dutch translations', () => {
		for (const key of ['Shared with me', 'Shared']) {
			expect(Object.hasOwn(en, key), key).toBe(true);
			expect((nl as Record<string, string>)[key], key).toBeTruthy();
		}
	});
});
