import type { AgentDocument } from './agentDocument';

export interface DocumentGroup {
	title: string;
	// Positions in the chat's document list, oldest version first.
	indices: number[];
}

// A document carries no id, so outputs with the same title are versions of one
// document. An untitled output cannot be matched and stands on its own.
export function groupDocuments(documents: Pick<AgentDocument, 'title'>[]): DocumentGroup[] {
	const groups: DocumentGroup[] = [];
	const byTitle = new Map<string, DocumentGroup>();
	documents.forEach((document, index) => {
		const title = document.title.trim();
		const existing = title ? byTitle.get(title) : undefined;
		if (existing) {
			existing.indices.push(index);
			return;
		}
		const group = { title, indices: [index] };
		groups.push(group);
		if (title) byTitle.set(title, group);
	});
	return groups;
}
