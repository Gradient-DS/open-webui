// [Claude] The `chat:message:notices` event of a v2 agent turn: the lines shown above its answer for chat items
// the turn ran without, and the ids of the deleted ones the backend dropped from the chat's files.

export type TurnNotices = { notices: string[]; removed: string[] };

const strings = (value: unknown): string[] =>
	Array.isArray(value)
		? value.filter((item): item is string => typeof item === 'string' && item !== '')
		: [];

export function turnNotices(data: unknown): TurnNotices {
	const event = (data ?? {}) as Record<string, unknown>;
	return { notices: strings(event.notices), removed: strings(event.removed) };
}

/** The chat's selection without the removed entries, so the next turn does not send them again. */
export function withoutRemoved<T extends { id?: string }>(files: T[], removed: string[]): T[] {
	if (removed.length === 0) return files;
	return files.filter((file) => !(file.id && removed.includes(file.id)));
}
