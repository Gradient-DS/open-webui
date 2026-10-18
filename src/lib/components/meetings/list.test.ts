import { beforeEach, describe, expect, it, vi } from 'vitest';
import { get } from 'svelte/store';

const getMeetings = vi.fn();
vi.mock('$lib/apis/meetings', () => ({ getMeetings: (token: string) => getMeetings(token) }));

const { forgetMeeting, meetingList, refreshMeetings } = await import('./list');

const meeting = (thread_id: string) => ({
	thread_id,
	agent: 'meeting',
	created_at: '',
	title: null
});

describe('meeting list', () => {
	beforeEach(() => getMeetings.mockReset());

	it('shares one request between callers and keeps the result', async () => {
		getMeetings.mockResolvedValue({ data: [meeting('a')] });
		await Promise.all([refreshMeetings('t1'), refreshMeetings('t1')]);
		expect(getMeetings).toHaveBeenCalledTimes(1);
		expect(get(meetingList)).toEqual([meeting('a')]);
	});

	it('keeps the shown list when a refresh fails', async () => {
		getMeetings.mockResolvedValueOnce({ data: [meeting('a')] }).mockRejectedValueOnce(new Error());
		await refreshMeetings('t2');
		await expect(refreshMeetings('t2')).rejects.toThrow();
		expect(get(meetingList)).toEqual([meeting('a')]);
	});

	it('drops the kept list when the user changes', async () => {
		let resolve!: (value: unknown) => void;
		getMeetings.mockResolvedValueOnce({ data: [meeting('a')] });
		await refreshMeetings('t3');
		getMeetings.mockReturnValueOnce(new Promise((r) => (resolve = r)));
		const next = refreshMeetings('t4');
		expect(get(meetingList)).toBeNull();
		resolve({ data: [meeting('b')] });
		await next;
		expect(get(meetingList)).toEqual([meeting('b')]);
	});

	it('forgets a deleted meeting', async () => {
		getMeetings.mockResolvedValue({ data: [meeting('a'), meeting('b')] });
		await refreshMeetings('t5');
		forgetMeeting('a');
		expect(get(meetingList)).toEqual([meeting('b')]);
	});
});
