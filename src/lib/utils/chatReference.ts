type ChatReferenceItem = {
	id?: string;
	thread_id?: string;
	owned?: boolean;
	readonly?: boolean;
	organization?: { id: string } | null;
	updated_at: number | string;
};

export function chatReference<T extends ChatReferenceItem>(item: T) {
	return {
		...item,
		id:
			item.id ??
			((item.readonly ?? item.owned === false)
				? item.thread_id
				: (item.organization?.id ?? item.thread_id)),
		updated_at:
			typeof item.updated_at === 'string' ? Date.parse(item.updated_at) / 1000 : item.updated_at
	};
}
