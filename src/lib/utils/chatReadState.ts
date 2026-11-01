export type ChatReadState = {
	id?: string;
	answered_at?: string | null;
	updated_at?: number | null;
	last_read_at?: number | null;
	active?: boolean;
};

export const answerTimestamp = (answeredAt?: string | null): number | null => {
	if (!answeredAt) return null;
	const timestamp = Date.parse(answeredAt) / 1000;
	return Number.isFinite(timestamp) ? timestamp : null;
};

export const isChatUnread = (chat: ChatReadState, v2: boolean, openChatId = ''): boolean => {
	if (chat.id && chat.id === openChatId) return false;
	if (v2) {
		const answeredAt = answerTimestamp(chat.answered_at);
		return answeredAt !== null && answeredAt > (chat.last_read_at ?? 0);
	}
	return (
		!chat.active &&
		(chat.last_read_at == null || (chat.updated_at != null && chat.updated_at > chat.last_read_at))
	);
};
