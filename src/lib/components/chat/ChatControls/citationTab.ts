import {
	reduceSources,
	type DisplayCitation,
	type RawSource
} from '../Messages/Citations/reduceSources';
import { usedCitations } from '../Messages/Citations/panelScope';
import {
	calculateShowRelevance,
	shouldShowPercentage
} from '../Messages/Citations/relevanceDisplay';

export interface CitationMessage {
	id: string;
	role: string;
	parentId?: string | null;
	content?: string;
	sources?: RawSource[];
}

// [Gradient] Keep cumulative citations for inline references and used sources for each question.
export interface SourceGroup {
	messageId: string;
	question: string;
	citations: DisplayCitation[];
	used: DisplayCitation[];
	showPercentage: boolean;
	showRelevance: boolean;
}

export function sourceGroups(history: CitationHistory | null | undefined): SourceGroup[] {
	const groups: SourceGroup[] = [];
	let id = history?.currentId;
	const visited = new Set<string>();
	while (id && !visited.has(id)) {
		visited.add(id);
		const message = history?.messages?.[id];
		if (!message) break;
		if (message.role === 'assistant' && message.sources?.length) {
			const parent = message.parentId ? history?.messages?.[message.parentId] : undefined;
			const citations = reduceSources(message.sources);
			const used = usedCitations(citations);
			groups.push({
				messageId: message.id,
				question: parent?.role === 'user' ? (parent.content ?? '').replace(/\s+/g, ' ').trim() : '',
				citations,
				used,
				showPercentage: shouldShowPercentage(used),
				showRelevance: calculateShowRelevance(used)
			});
		}
		id = message.parentId;
	}
	return groups.reverse();
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
