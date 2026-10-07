// [Gradient] Vergadering: thin client for the meetings proxy (routers/meetings.py).
import { WEBUI_API_BASE_URL } from '$lib/constants';
import type {
	MeetingAgent,
	MeetingInput,
	MeetingRead,
	MeetingSummary
} from '$lib/components/meetings/meeting';

export class MeetingApiError extends Error {
	status: number;
	detail: unknown;

	constructor(status: number, detail: unknown) {
		const message =
			typeof detail === 'string'
				? detail
				: ((detail as { detail?: string })?.detail ?? `HTTP ${status}`);
		super(message);
		this.status = status;
		this.detail = detail;
	}
}

const request = async <T>(token: string, path: string, init: RequestInit = {}): Promise<T> => {
	const res = await fetch(`${WEBUI_API_BASE_URL}/meetings${path}`, {
		...init,
		headers: {
			Accept: 'application/json',
			...(init.body && !(init.body instanceof Blob) ? { 'Content-Type': 'application/json' } : {}),
			authorization: `Bearer ${token}`,
			...(init.headers ?? {})
		}
	});
	if (!res.ok) {
		const body = await res.json().catch(() => null);
		throw new MeetingApiError(res.status, body?.detail ?? body);
	}
	return res.status === 204 ? (undefined as T) : ((await res.json()) as T);
};

export const getMeetingAgents = (token: string) =>
	request<{ data: MeetingAgent[] }>(token, '/agents');

export const getMeetings = (token: string) => request<{ data: MeetingSummary[] }>(token, '');

export const getMeeting = (token: string, id: string) =>
	request<MeetingRead>(token, `/${encodeURIComponent(id)}`);

export const startMeeting = (token: string, input: MeetingInput) =>
	request<{ id: string }>(token, '', { method: 'POST', body: JSON.stringify({ input }) });

export const sendMeetingInput = (token: string, id: string, input: MeetingInput) =>
	request<{ id: string }>(token, `/${encodeURIComponent(id)}/inputs`, {
		method: 'POST',
		body: JSON.stringify({ input })
	});

export const deleteMeeting = (token: string, id: string) =>
	request<void>(token, `/${encodeURIComponent(id)}`, { method: 'DELETE' });

export const uploadMeetingAudio = (token: string, audio: Blob, name: string) =>
	request<Record<string, unknown>>(token, `/audio?name=${encodeURIComponent(name)}`, {
		method: 'POST',
		body: audio,
		headers: { 'Content-Type': audio.type || 'application/octet-stream' }
	});
