// [Gradient] The "+" menu's items in menu order, and which of them the composer bar shows:
// every pinned item, plus every item that is on or on Auto, pinned or not.

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

/**
 * The ids the composer bar renders: pinned or active (on/auto), allowed here, in menu order,
 * each once. Pins for items that no longer exist are ignored.
 */
export const barItemIds = ({
	order,
	pinned,
	states,
	allowed = () => true
}: {
	order: readonly string[];
	pinned: readonly string[];
	states: Readonly<Record<string, InputItemState | undefined>>;
	allowed?: (id: string) => boolean;
}): string[] => {
	const pins = new Set(pinned);
	return [...new Set(order)].filter(
		(id) => allowed(id) && (pins.has(id) || (states[id] ?? 'off') !== 'off')
	);
};
