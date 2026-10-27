type SidebarChat = { id: string; owned?: boolean; [key: string]: unknown };

/** Only the platform's `owned: false` marks a chat as shared; v1 rows carry no flag. */
export const isSharedChat = (chat: SidebarChat): boolean => chat.owned === false;

/** Own chats stay in the chat and pinned lists; everything shared goes to its own section. */
export const splitChatsByOwnership = <T extends SidebarChat>(
	chats: T[] | null,
	pinned: T[]
): { own: T[] | null; pinned: T[]; shared: T[] } => {
	const seen = new Set<string>();
	const shared = [...pinned, ...(chats ?? [])].filter(
		(chat) => isSharedChat(chat) && !seen.has(chat.id) && seen.add(chat.id)
	);
	return {
		own: chats === null ? null : chats.filter((chat) => !isSharedChat(chat)),
		pinned: pinned.filter((chat) => !isSharedChat(chat)),
		shared
	};
};
