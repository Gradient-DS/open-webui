import { expect, it } from 'vitest';
import { answerTimestamp, isChatUnread } from './chatReadState';

const answer = '2026-11-01T12:00:00.500Z';
const readAt = Date.parse(answer) / 1000;
const chat = { id: 'conversation', answered_at: answer, last_read_at: readAt };

it.each(['rename', 'tags', 'pin', 'folder'])('%s does not create an unread answer', (change) => {
	expect(isChatUnread({ ...chat, updated_at: readAt + 100, [change]: 'changed' }, true)).toBe(
		false
	);
});

it('shows a finished answer received while away, including within the same second', () => {
	expect(isChatUnread({ ...chat, answered_at: '2026-11-01T12:00:00.750Z' }, true, 'other')).toBe(
		true
	);
});

it('clears the dot while the conversation is open and after its read is persisted', () => {
	const unread = { ...chat, last_read_at: readAt - 1 };
	expect(isChatUnread(unread, true)).toBe(true);
	expect(isChatUnread(unread, true, chat.id)).toBe(false);
	expect(isChatUnread({ ...unread, last_read_at: readAt }, true, 'other')).toBe(false);
});

it('does not leave a dot when the answer finishes while open', () => {
	const finished = { ...chat, answered_at: '2026-11-01T12:01:00.250Z' };
	expect(isChatUnread(finished, true, chat.id)).toBe(false);
	expect(
		isChatUnread({ ...finished, last_read_at: answerTimestamp(finished.answered_at) }, true)
	).toBe(false);
});

it.each([undefined, null, '', 'invalid'])(
	'shows no dot without a usable answered_at: %s',
	(answered_at) => {
		expect(
			isChatUnread({ ...chat, answered_at, last_read_at: null, updated_at: readAt + 100 }, true)
		).toBe(false);
	}
);

it('marks a first completed answer unread when it has never been read', () => {
	expect(isChatUnread({ ...chat, last_read_at: null }, true)).toBe(true);
});

it('preserves the legacy unread rule', () => {
	expect(isChatUnread({ id: 'legacy', updated_at: 2, last_read_at: 1 }, false)).toBe(true);
	expect(isChatUnread({ id: 'legacy', updated_at: 2, last_read_at: 1, active: true }, false)).toBe(
		false
	);
});
