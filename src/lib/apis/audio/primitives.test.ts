import i18next from 'i18next';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { addNewMemory, queryMemory } from '../memories';
import { transcribeAudio } from './index';

vi.mock('$app/environment', () => ({ browser: false, dev: false }));

const calls = [
	() => addNewMemory('fictional-token', 'Prefers fictional tea.'),
	() => queryMemory('fictional-token', 'What tea?'),
	() => transcribeAudio('fictional-token', new Blob(['fictional audio']) as File, 'nl')
];

afterEach(() => {
	vi.restoreAllMocks();
	vi.unstubAllGlobals();
});

describe('platform primitive errors', () => {
	it.each(calls)('shows the translated platform message', async (call) => {
		await i18next.init({ lng: 'nl-NL', resources: { 'nl-NL': { translation: {} } } });
		const fetch = vi.fn().mockResolvedValue({
			ok: false,
			json: async () => ({
				detail: {
					code: 'budget_spent',
					detail: 'Het platform kon dit verzoek niet uitvoeren.'
				}
			})
		});
		vi.stubGlobal('fetch', fetch);
		vi.spyOn(console, 'error').mockImplementation(() => {});
		await expect(call()).rejects.toBe('Het platform kon dit verzoek niet uitvoeren.');
		expect(fetch.mock.calls[0][1].headers['Accept-Language']).toBe('nl-NL');
	});

	it.each(calls)('keeps legacy plain-string errors readable', async (call) => {
		vi.stubGlobal(
			'fetch',
			vi.fn().mockResolvedValue({
				ok: false,
				json: async () => ({ detail: 'Transcription failed.' })
			})
		);
		vi.spyOn(console, 'error').mockImplementation(() => {});
		await expect(call()).rejects.toBe('Transcription failed.');
	});
});
