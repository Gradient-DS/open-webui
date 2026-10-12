// [Gradient] Vergadering: the caller's meeting list, kept for the session so a revisit renders at once
// while a refresh runs. Every soev-api list call scans each meeting's events for its status.
import { writable } from 'svelte/store';

import { getMeetings } from '$lib/apis/meetings';
import type { MeetingSummary } from './meeting';

/** null until this session's first successful load. */
export const meetingList = writable<MeetingSummary[] | null>(null);

let owner: string | undefined;
let inflight: Promise<MeetingSummary[]> | null = null;

/** Loads the list into `meetingList`, sharing a request already in flight; rejects on failure. */
export const refreshMeetings = (token: string): Promise<MeetingSummary[]> => {
	if (owner !== token) {
		owner = token;
		inflight = null;
		meetingList.set(null);
	}
	if (inflight) return inflight;
	const request = getMeetings(token)
		.then((res) => {
			const data = res?.data ?? [];
			if (owner === token) meetingList.set(data);
			return data;
		})
		.finally(() => {
			if (inflight === request) inflight = null;
		});
	inflight = request;
	return request;
};

/** Drops a deleted meeting from the kept list. */
export const forgetMeeting = (id: string) =>
	meetingList.update((list) => list?.filter((meeting) => meeting.thread_id !== id) ?? null);
