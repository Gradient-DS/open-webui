import { describe, expect, it } from 'vitest';

import type { MeetingState } from './meeting';
import { parseData, reduceStream, startStream, type StreamFrame } from './stream';

const snapshot = (extra: Partial<MeetingState>): { payload: MeetingState } => ({
	payload: { status: 'ready', transcript: null, ...extra }
});

const fold = (frames: StreamFrame[], kind: 'summary' | 'minutes' | 'actions' = 'summary') =>
	frames.reduce(reduceStream, startStream(kind));

describe('output stream', () => {
	it('adds deltas up and ends on the final snapshot and status', () => {
		const final = snapshot({
			pending_action: null,
			outputs: { summary: { markdown: 'Kort overleg.' } }
		});
		const stream = fold([
			{ event: 'input', data: {} },
			{ event: 'meeting_state', data: snapshot({ pending_action: 'summary' }) },
			{ event: 'delta', data: { text: 'Kort ' } },
			{ event: 'delta', data: { text: 'overleg.' } },
			{ event: 'meeting_state', data: final },
			{ event: 'status', data: { state: 'idle' } }
		]);
		expect(stream.text).toBe('Kort overleg.');
		expect(stream.final).toEqual(final.payload);
		expect(stream.done).toBe(true);
	});

	it('takes the output kind from the progress snapshot', () => {
		const stream = fold([
			{ event: 'meeting_state', data: snapshot({ pending_action: 'minutes' }) }
		]);
		expect(stream.kind).toBe('minutes');
		expect(stream.final).toBeNull();
	});

	it('ignores deltas after the final snapshot and malformed frames', () => {
		const stream = fold([
			{ event: 'delta', data: { text: 'a' } },
			{ event: 'meeting_state', data: snapshot({ pending_action: null }) },
			{ event: 'delta', data: { text: 'b' } },
			{ event: 'delta', data: 'not json' },
			{ event: 'meeting_state', data: {} }
		]);
		expect(stream.text).toBe('a');
	});

	it('records an error frame as the end of the stream', () => {
		const stream = fold([
			{ event: 'error', data: { code: 'x', detail: 'Chat stream disconnected' } }
		]);
		expect(stream).toMatchObject({ done: true, error: 'Chat stream disconnected', final: null });
	});

	it('parses JSON data and keeps anything else as text', () => {
		expect(parseData('{"text":"hé"}')).toEqual({ text: 'hé' });
		expect(parseData('plain')).toBe('plain');
	});
});
