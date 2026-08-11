/**
 * Strict data-separation classification (soev data-sovereignty feature).
 *
 * A conversation may use the open internet (web search / webpage URLs) OR
 * internal documents (files / knowledge bases / notes), never both. These pure
 * helpers classify attachments and resolve the conversation's active side.
 *
 * Mirrors `backend/open_webui/utils/data_separation.py` — keep the two
 * classifications in sync.
 */

export type DataSide = 'open_internet' | 'internal';

export interface FileItem {
	type?: string;
	url?: string;
	[key: string]: unknown;
}

export interface ChatMessage {
	files?: FileItem[] | null;
	[key: string]: unknown;
}

const INTERNAL_FILE_TYPES = new Set(['file', 'image', 'collection', 'folder', 'chat', 'note']);

/** Classify a single chat `files[]` item, or null if it belongs to neither group. */
export function classifyFileItem(item: FileItem | null | undefined): DataSide | null {
	if (!item || typeof item !== 'object') {
		return null;
	}
	if (item.type === 'web_search') {
		return 'open_internet';
	}
	// `url` — a web page attached for the agent to fetch live (GRA-222). Same
	// side as an ingested page: the content comes off the open internet either
	// way, only the moment of fetching differs.
	if (item.type === 'url') {
		return 'open_internet';
	}
	if (item.type === 'text' && item.url) {
		return 'open_internet';
	}
	if (item.type && INTERNAL_FILE_TYPES.has(item.type)) {
		return 'internal';
	}
	return null;
}

/** The side selected in the message currently being composed (or null). */
export function getLiveSide(
	files: FileItem[] | null | undefined,
	webSearchEnabled: boolean
): DataSide | null {
	if (webSearchEnabled) {
		return 'open_internet';
	}
	for (const item of files ?? []) {
		const side = classifyFileItem(item);
		if (side) {
			return side;
		}
	}
	return null;
}

/** The side already committed by prior (sent) messages (or null). */
export function getHistorySide(messages: ChatMessage[] | null | undefined): DataSide | null {
	for (const message of messages ?? []) {
		for (const item of message?.files ?? []) {
			const side = classifyFileItem(item);
			if (side) {
				return side;
			}
		}
	}
	return null;
}

/**
 * The effective active side for a conversation: a committed history side locks
 * the conversation; otherwise the live composition side applies. Null means
 * both sides are still available (first message, nothing selected yet).
 */
export function getActiveSide({
	messages,
	files,
	webSearchEnabled
}: {
	messages: ChatMessage[] | null | undefined;
	files: FileItem[] | null | undefined;
	webSearchEnabled: boolean;
}): DataSide | null {
	return getHistorySide(messages) ?? getLiveSide(files, webSearchEnabled);
}
