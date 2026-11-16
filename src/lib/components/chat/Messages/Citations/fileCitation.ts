// [Gradient] Chat file chips open in the sources panel as a whole-document source.
import type { ChatAttachment } from '$lib/types/chatAttachment';
import type { DisplayCitation } from './reduceSources';

/** Context key Chat.svelte provides; FileItem falls back to its modal without it. */
export const CHAT_FILE_PANEL = 'chat-file-panel';
export type OpenFileInPanel = (item: ChatAttachment & { id: string }) => boolean;

/** A stored chat file, also while it is still processing (the composer then reports `uploading`). */
export function opensInPanel(
	item: ChatAttachment | null | undefined
): item is ChatAttachment & { id: string } {
	return item?.type === 'file' && typeof item.id === 'string' && item.id !== '';
}

const inlineText = (item: ChatAttachment): string => {
	const content = typeof item.file === 'object' ? item.file?.data?.content : undefined;
	if (typeof content === 'string' && content) return content;
	return typeof item.content === 'string' ? item.content : '';
};

export function fileCitation(item: ChatAttachment & { id: string }): DisplayCitation {
	const name = item.name ?? '';
	const source = item.source as { provider?: unknown } | undefined;
	const provider = typeof source?.provider === 'string' ? source.provider : undefined;
	const webUrl =
		typeof item.web_url === 'string' && item.web_url.startsWith('https://')
			? item.web_url
			: undefined;
	return {
		id: item.id,
		source: { id: item.id, name, type: 'file', ...(provider ? { provider } : {}) },
		document: [inlineText(item)],
		metadata: [
			{
				file_id: item.id,
				name,
				granularity: 'document',
				...(webUrl ? { source_url: webUrl } : {})
			}
		],
		distances: [undefined],
		n: 0
	};
}
