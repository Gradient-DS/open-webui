// [Gradient] Vergadering: fold one action turn's SSE frames (contract C6) into what the tab shows.
import type { MeetingState, OutputKind } from './meeting';

export type StreamFrame = { event: string; data: unknown };

export type OutputStream = {
	/** The output the deltas belong to (from the progress snapshot, else the one asked for). */
	kind: OutputKind;
	/** Markdown written so far. */
	text: string;
	/** The final snapshot; once set, it is the source of truth. */
	final: MeetingState | null;
	/** The turn's stream has ended (status or error frame). */
	done: boolean;
	error: string | null;
};

export const startStream = (kind: OutputKind): OutputStream => ({
	kind,
	text: '',
	final: null,
	done: false,
	error: null
});

const payloadOf = (data: unknown): MeetingState | null => {
	const payload = (data as { payload?: unknown })?.payload;
	return payload && typeof payload === 'object' ? (payload as MeetingState) : null;
};

export const reduceStream = (stream: OutputStream, frame: StreamFrame): OutputStream => {
	switch (frame.event) {
		case 'meeting_state': {
			const snapshot = payloadOf(frame.data);
			if (!snapshot) return stream;
			if (snapshot.pending_action) return { ...stream, kind: snapshot.pending_action };
			return { ...stream, final: snapshot };
		}
		case 'delta': {
			const text = (frame.data as { text?: unknown })?.text;
			return typeof text === 'string' && !stream.final
				? { ...stream, text: stream.text + text }
				: stream;
		}
		case 'status':
			return { ...stream, done: true };
		case 'error': {
			const detail = (frame.data as { detail?: unknown })?.detail;
			return { ...stream, done: true, error: typeof detail === 'string' ? detail : 'error' };
		}
		default:
			return stream;
	}
};

/** Parses a JSON SSE data field; anything else is passed on as text. */
export const parseData = (data: string): unknown => {
	try {
		return JSON.parse(data);
	} catch {
		return data;
	}
};
