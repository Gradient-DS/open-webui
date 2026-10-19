// [Gradient] The "+" menu's items in menu order, and which of them the composer bar shows:
// every pinned item, every item that is on or on Auto, and every item used earlier in this
// chat, so switching one off never takes its button away mid-chat.

export type InputItemSection = 'context' | 'knowledge' | 'tools';

/** off: not used; auto: the model decides; on: always used (or attached/selected). */
export type InputItemState = 'off' | 'auto' | 'on';

export type InputItem = { id: string; section: InputItemSection; pinnable: true };

/** Menu order. `filter:*` stands for the toggleable filters, in their own order. */
export const INPUT_ITEMS: readonly InputItem[] = [
	{ id: 'upload_files', section: 'context', pinnable: true },
	{ id: 'capture', section: 'context', pinnable: true },
	{ id: 'attach_webpage', section: 'context', pinnable: true },
	{ id: 'attach_files', section: 'context', pinnable: true },
	{ id: 'attach_notes', section: 'context', pinnable: true },
	{ id: 'attach_meetings', section: 'context', pinnable: true },
	{ id: 'google_drive', section: 'context', pinnable: true },
	{ id: 'onedrive', section: 'context', pinnable: true },
	{ id: 'knowledge', section: 'knowledge', pinnable: true },
	{ id: 'reference_chats', section: 'knowledge', pinnable: true },
	{ id: 'live_mail', section: 'tools', pinnable: true },
	{ id: 'live_documents', section: 'tools', pinnable: true },
	{ id: 'web_search', section: 'tools', pinnable: true },
	{ id: 'image_generation', section: 'tools', pinnable: true },
	{ id: 'code_interpreter', section: 'tools', pinnable: true },
	{ id: 'document_writer', section: 'tools', pinnable: true },
	{ id: 'filter:*', section: 'tools', pinnable: true },
	{ id: 'tools', section: 'tools', pinnable: true },
	{ id: 'skills', section: 'tools', pinnable: true }
];

/** All item ids in menu order, with the filter placeholder expanded. */
export const inputItemOrder = (filterIds: readonly string[] = []): string[] =>
	INPUT_ITEMS.flatMap((item) =>
		item.id === 'filter:*' ? filterIds.map((id) => `filter:${id}`) : [item.id]
	);

/** Tool states ('off' | 'auto' | 'required') and booleans as one item state. */
export const itemState = (value: string | boolean | number | null | undefined): InputItemState => {
	if (value === 'auto') return 'auto';
	if (value === 'off' || !value) return 'off';
	return 'on';
};

/** The stored pin set, cleaned: strings only, each once, in stored order. */
export const normalizePins = (raw: unknown): string[] =>
	Array.isArray(raw)
		? [...new Set(raw.filter((id): id is string => typeof id === 'string' && id.length > 0))]
		: [];

/** `used` plus every item that is active now; the same array when nothing was added. */
export const usedItemIds = (
	used: readonly string[],
	states: Readonly<Record<string, InputItemState | undefined>>
): readonly string[] => {
	const added = Object.keys(states).filter(
		(id) => (states[id] ?? 'off') !== 'off' && !used.includes(id)
	);
	return added.length ? [...used, ...added] : used;
};

/**
 * The ids the composer bar renders: pinned, active (on/auto) or used earlier in this chat,
 * allowed here, in menu order, each once. Pins for items that no longer exist are ignored.
 */
export const barItemIds = ({
	order,
	pinned,
	states,
	used = [],
	allowed = () => true
}: {
	order: readonly string[];
	pinned: readonly string[];
	states: Readonly<Record<string, InputItemState | undefined>>;
	used?: readonly string[];
	allowed?: (id: string) => boolean;
}): string[] => {
	const shown = new Set([...pinned, ...used]);
	return [...new Set(order)].filter(
		(id) => allowed(id) && (shown.has(id) || (states[id] ?? 'off') !== 'off')
	);
};
