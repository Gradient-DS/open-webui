import { get, readonly, writable } from 'svelte/store';
import { getChatList, getPinnedChatList, markChatReadById } from '$lib/apis/chats';
import { answerTimestamp } from '$lib/utils/chatReadState';

type ChatListItem = {
	id: string;
	readonly?: boolean;
	owned?: boolean;
	answered_at?: string | null;
	[key: string]: unknown;
};

const chatsStore = writable<ChatListItem[] | null>(null);
const pinnedChatsStore = writable<ChatListItem[]>([]);

export const chats = readonly(chatsStore);
export const pinnedChats = readonly(pinnedChatsStore);
const readAtStore = writable<Record<string, number>>({});
let readSession = 0;
export const chatReadAt = readonly(readAtStore);

export const clearChatReadAt = (chatId: string) => {
	readAtStore.update(({ [chatId]: ignored, ...rest }) => rest);
};

export const markConversationRead = async (token: string, conversationId: string) => {
	const session = readSession;
	const result = await markChatReadById(token, conversationId);
	if (session === readSession) setChatReadAt(result.chat_id, result.last_read_at);
	return result;
};

let currentPage = 1;
let paginationReady = false;
let requestGeneration = 0;
let allLoaded = false;
let loadingNextPage = false;

type RefreshChatListOptions = {
	refreshPinned?: boolean;
	clearPinned?: boolean;
};

type ChatListResult = {
	accepted: boolean;
	allLoaded: boolean;
};

export const refreshChatList = async (
	token: string = '',
	options: RefreshChatListOptions = {}
): Promise<ChatListResult> => {
	const generation = ++requestGeneration;
	paginationReady = false;
	loadingNextPage = false;

	const [nextChats, nextPinnedChats] = await Promise.all([
		getChatList(token, 1) as Promise<ChatListItem[]>,
		options.refreshPinned && !options.clearPinned
			? (getPinnedChatList(token) as Promise<ChatListItem[]>)
			: Promise.resolve(undefined as ChatListItem[] | undefined)
	]);

	if (generation !== requestGeneration) {
		return { accepted: false, allLoaded };
	}

	chatsStore.set(nextChats);
	currentPage = 1;
	allLoaded = nextChats.length === 0;

	if (options.clearPinned) {
		pinnedChatsStore.set([]);
	} else if (options.refreshPinned) {
		pinnedChatsStore.set(nextPinnedChats ?? []);
	}

	paginationReady = true;
	return { accepted: true, allLoaded };
};

// The sidebar owns folder state. This bridge lets other components refresh it.
type FolderRefreshHandler = (folderId?: string | null, chat?: ChatListItem | null) => unknown;
const folderRefreshHandlers = new Set<FolderRefreshHandler>();

export const registerFolderRefreshHandler = (handler: FolderRefreshHandler) => {
	folderRefreshHandlers.add(handler);
	return () => {
		folderRefreshHandlers.delete(handler);
	};
};

export const refreshFolderChatLists = async (
	folderId?: string | null,
	chat?: ChatListItem | null
) => {
	await Promise.all([...folderRefreshHandlers].map((handler) => handler(folderId, chat)));
};

export const loadNextChatListPage = async (token: string = ''): Promise<ChatListResult> => {
	if (!paginationReady || allLoaded || loadingNextPage) {
		return { accepted: false, allLoaded };
	}

	const generation = requestGeneration;
	const nextPage = currentPage + 1;
	loadingNextPage = true;

	try {
		const nextChats = (await getChatList(token, nextPage)) as ChatListItem[];

		if (generation !== requestGeneration) {
			return { accepted: false, allLoaded };
		}

		allLoaded = nextChats.length === 0;
		currentPage = nextPage;

		const existingIds = new Set((get(chatsStore) ?? []).map((chat) => chat.id));
		const uniqueChats = nextChats.filter((chat) => !existingIds.has(chat.id));
		chatsStore.set([...(get(chatsStore) ?? []), ...uniqueChats]);

		return { accepted: true, allLoaded };
	} finally {
		loadingNextPage = false;
	}
};

export const setChatActive = (chatId: string, active: boolean): boolean => {
	let found = false;
	const updateChat = (chat: ChatListItem) => {
		if (chat.id !== chatId) {
			return chat;
		}
		found = true;
		return { ...chat, active };
	};

	chatsStore.update((items) => (items ? items.map(updateChat) : items));
	pinnedChatsStore.update((items) => items.map(updateChat));
	return found;
};

export const setChatReadAt = (chatId: string, lastReadAt: number): boolean => {
	readAtStore.update((reads) => ({
		...reads,
		[chatId]: Math.max(reads[chatId] ?? 0, lastReadAt)
	}));
	let found = false;
	const updateChat = (chat: ChatListItem) => {
		if (chat.id !== chatId) {
			return chat;
		}
		found = true;
		return { ...chat, last_read_at: lastReadAt };
	};

	chatsStore.update((items) => (items ? items.map(updateChat) : items));
	pinnedChatsStore.update((items) => items.map(updateChat));
	return found;
};

export const setAllChatsRead = (v2 = false) => {
	const updateChat = (chat: ChatListItem) => ({
		...chat,
		last_read_at: v2 ? answerTimestamp(chat.answered_at) : chat.updated_at
	});

	chatsStore.update((items) => (items ? items.map(updateChat) : items));
	pinnedChatsStore.update((items) => items.map(updateChat));
};

export const resetChatListState = () => {
	readSession += 1;
	requestGeneration += 1;
	currentPage = 1;
	paginationReady = false;
	allLoaded = false;
	loadingNextPage = false;
	chatsStore.set(null);
	pinnedChatsStore.set([]);
	readAtStore.set({});
};
