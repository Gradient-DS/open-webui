import i18next from 'i18next';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { addNewMemory, queryMemory } from '../memories';
import { synthesizeSpeech, transcribeAudio } from './index';

vi.mock('$app/environment', () => ({ browser: false, dev: false }));

const calls = [
	() => addNewMemory('fictional-token', 'Prefers fictional tea.'),
	() => queryMemory('fictional-token', 'What tea?'),
	() => transcribeAudio('fictional-token', new Blob(['fictional audio']) as File, 'nl'),
	() => synthesizeSpeech('fictional-token', '', 'Fictional speech.')
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

it.each(['', 'fictional-voice'])(
	'speech sends only primitive fields and leaves the response stream readable',
	async (voice) => {
		const response = new Response('fictional audio', { headers: { 'Content-Type': 'audio/mpeg' } });
		const fetch = vi.fn().mockResolvedValue(response);
		vi.stubGlobal('fetch', fetch);
		const result = await synthesizeSpeech('fictional-token', voice, 'Fictional speech.');
		expect(result).toBe(response);
		expect(response.bodyUsed).toBe(false);
		const [url, options] = fetch.mock.calls[0];
		expect(url).toMatch(/\/audio\/speech$/);
		expect(options.headers.Authorization).toBe('Bearer fictional-token');
		expect(JSON.parse(options.body)).toEqual({
			input: 'Fictional speech.',
			response_format: 'mp3',
			...(voice && { voice })
		});
	}
);
