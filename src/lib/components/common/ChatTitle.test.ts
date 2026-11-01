import { describe, expect, it } from 'vitest';
import { createInstance } from 'i18next';
import { render } from 'svelte/server';
import { writable } from 'svelte/store';
import en from '$lib/i18n/locales/en-US/translation.json';
import nl from '$lib/i18n/locales/nl-NL/translation.json';
import ChatTitle from './ChatTitle.svelte';

const translations = { 'en-US': en, 'nl-NL': nl };

describe.each([
	['en-US', 'New Chat'],
	['nl-NL', 'Nieuwe Chat']
])('chat list titles in %s', (language, fallback) => {
	const show = async (props: { title?: string | null }) => {
		const i18n = createInstance();
		await i18n.init({
			lng: language,
			returnEmptyString: false,
			keySeparator: false,
			nsSeparator: false,
			resources: Object.fromEntries(
				Object.entries(translations).map(([locale, translation]) => [locale, { translation }])
			)
		});
		return render(ChatTitle, {
			props,
			context: new Map([['i18n', writable(i18n)]])
		}).body;
	};

	it.each(['', null, undefined])('displays a translated fallback for title %j', async (title) => {
		const props = Object.freeze({ title });
		expect(await show(props)).toContain(fallback);
		expect(props.title).toBe(title);
	});

	it('displays the fallback when the platform omits the title', async () => {
		expect(await show({})).toContain(fallback);
	});

	it('displays the generated title once it arrives', async () => {
		const props = { title: '' };
		expect(await show(props)).toContain(fallback);
		props.title = 'A generated title';
		const html = await show(props);
		expect(html).toContain(props.title);
		expect(html).not.toContain(fallback);
	});
});
