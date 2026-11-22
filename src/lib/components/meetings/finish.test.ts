// [Gradient] Finishing keeps every recording and its input in the meeting's thread.
import { afterEach, describe, expect, it, vi } from 'vitest';

import { finishMeeting } from './finish';

const { remove } = vi.hoisted(() => ({ remove: vi.fn() }));
vi.mock('./parts', () => ({ partStore: () => ({ remove }) }));

afterEach(() => {
	vi.unstubAllGlobals();
	vi.resetAllMocks();
});

describe('finish meeting', () => {
	it('uploads every part under the meeting thread before finishing and clearing local parts', async () => {
		const recordings = [
			new Blob(['fictional first recording'], { type: 'audio/webm;codecs=opus' }),
			new Blob(['fictional second recording'], { type: 'audio/ogg' })
		];
		const references = recordings.map((_, index) => ({ id: `fictional-audio-${index}` }));
		const requests: { url: URL; init: RequestInit }[] = [];
		vi.stubGlobal(
			'fetch',
			vi.fn(async (url: string, init: RequestInit) => {
				requests.push({ url: new URL(url, 'https://meeting.example.invalid'), init });
				return Response.json(references[requests.length - 1] ?? { id: 'fictional-meeting' });
			})
		);
		remove.mockImplementation(async () => expect(requests).toHaveLength(3));

		await finishMeeting('fictional-token', 'fictional-meeting', recordings);

		expect(requests).toHaveLength(3);
		for (const [index, { url, init }] of requests.slice(0, 2).entries()) {
			expect(url.pathname).toBe('/api/v1/meetings/audio');
			expect(url.searchParams.get('thread_id')).toBe('fictional-meeting');
			expect(url.searchParams.get('name')).toBe(`meeting-${index + 1}.${index ? 'ogg' : 'webm'}`);
			expect(init.method).toBe('POST');
			expect(init.body).toBe(recordings[index]);
			expect(init.headers).toMatchObject({ authorization: 'Bearer fictional-token' });
		}
		expect(requests[2].url.pathname).toBe('/api/v1/meetings/fictional-meeting/inputs');
		expect(JSON.parse(requests[2].init.body as string)).toEqual({
			input: { type: 'finish', audio_refs: references }
		});
		expect(remove).toHaveBeenCalledExactlyOnceWith('fictional-meeting');
	});

	it('keeps local recordings when an upload fails and does not send finish', async () => {
		const fetch = vi.fn(async () => Response.json({ detail: 'refused' }, { status: 404 }));
		vi.stubGlobal('fetch', fetch);

		await expect(
			finishMeeting('fictional-token', 'fictional-meeting', [new Blob(['fictional recording'])])
		).rejects.toMatchObject({ status: 404 });

		expect(fetch).toHaveBeenCalledTimes(1);
		expect(remove).not.toHaveBeenCalled();
	});
});
