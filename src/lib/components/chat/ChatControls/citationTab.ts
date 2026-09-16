import type { RawSource } from '../Messages/Citations/reduceSources';

export interface CitationMessage {
	id: string;
	role: string;
	parentId?: string | null;
	sources?: RawSource[];
}

export interface CitationHistory {
	currentId?: string | null;
	messages?: Record<string, CitationMessage>;
}

// [Gradient] Follow the selected branch, ignoring sibling answers and insertion order.
export function latestMessageWithSources(
	history: CitationHistory | null | undefined
): CitationMessage | null {
	let id = history?.currentId;
	const visited = new Set<string>();
	while (id && !visited.has(id)) {
		visited.add(id);
		const message = history?.messages?.[id];
		if (!message) return null;
		if (message.role === 'assistant' && message.sources?.length) return message;
		id = message.parentId;
	}
	return null;
}
