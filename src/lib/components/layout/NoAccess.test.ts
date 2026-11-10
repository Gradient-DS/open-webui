import { expect, it, vi } from 'vitest';
import { render } from 'svelte/server';
import { writable } from 'svelte/store';
import i18next from 'i18next';
import en from '$lib/i18n/locales/en-US/translation.json';
import nl from '$lib/i18n/locales/nl-NL/translation.json';
import NoAccess from './NoAccess.svelte';

vi.mock('$lib/apis/auths', () => ({ userSignOut: vi.fn(), getLogoutRedirectUrl: vi.fn() }));

it.each([
	['en-US', 'You do not have access yet. Ask your administrator to add you.', 'Sign Out'],
	['nl-NL', 'Je hebt nog geen toegang. Vraag je beheerder om je toe te voegen.', 'Uitloggen']
])('renders the no-access page and sign-out button in %s', async (lng, message, signOut) => {
	const i18n = i18next.createInstance();
	await i18n.init({
		lng,
		resources: { 'en-US': { translation: en }, 'nl-NL': { translation: nl } },
		returnEmptyString: false
	});
	const html = render(NoAccess, { context: new Map([['i18n', writable(i18n)]]) }).body;
	expect(html).toContain(message);
	expect(html).toContain('<main');
	expect(html).toMatch(new RegExp(`<button[^>]*>${signOut}</button>`));
	expect(html).not.toContain('Reconnect');
});
