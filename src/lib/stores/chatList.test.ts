import { afterEach, expect, it, vi } from 'vitest';
import { get } from 'svelte/store';
import { getChatList, getPinnedChatList, markChatReadById } from '$lib/apis/chats';
import {
	chatReadAt,
	chats,
	clearChatReadAt,
	markConversationRead,
	refreshChatList,
	resetChatListState
} from './chatList';
import { isChatUnread } from '$lib/utils/chatReadState';

vi.mock('$lib/apis/chats', () => ({
	getChatList: vi.fn(),
	getPinnedChatList: vi.fn(),
	markChatReadById: vi.fn()
}));
afterEach(() => {
	resetChatListState();
	vi.resetAllMocks();
});

it('persists a conversation read and keeps it across metadata refreshes and version changes', async () => {
	const answered_at = '2026-11-01T12:00:00.500Z';
	const last_read_at = Date.parse(answered_at) / 1000;
	vi.mocked(markChatReadById).mockResolvedValue({ chat_id: 'conversation', last_read_at });
	await markConversationRead('token', 'conversation');
	expect(markChatReadById).toHaveBeenCalledExactlyOnceWith('token', 'conversation');
	vi.mocked(getChatList).mockResolvedValue([
		{
			id: 'conversation',
			thread_id: 'new-version',
			answered_at,
			updated_at: last_read_at + 100,
			last_read_at: 0
		}
	]);
	vi.mocked(getPinnedChatList).mockResolvedValue([]);
	await refreshChatList('token');
	const row = get(chats)![0];
	expect(isChatUnread({ ...row, last_read_at: get(chatReadAt)[row.id] }, true)).toBe(false);
	expect(Object.keys(get(chatReadAt))).toEqual(['conversation']);
	clearChatReadAt('conversation');
	expect(get(chatReadAt)).toEqual({});
});

it('does not invent persisted reads when the server refuses access', async () => {
	vi.mocked(markChatReadById).mockRejectedValue('unavailable');
	await expect(markConversationRead('token', 'conversation')).rejects.toBe('unavailable');
	expect(get(chatReadAt)).toEqual({});
});

it('discards an acknowledgement from a session that has ended', async () => {
	let resolve!: (result: { chat_id: string; last_read_at: number }) => void;
	vi.mocked(markChatReadById).mockReturnValue(new Promise((done) => (resolve = done)));
	const pending = markConversationRead('old-token', 'conversation');
	resetChatListState();
	resolve({ chat_id: 'conversation', last_read_at: 100 });
	await pending;
	expect(get(chatReadAt)).toEqual({});
});
