import { decode } from 'html-entities';

export type DocumentFormat = 'markdown' | 'html';

export interface AgentDocument {
	title: string;
	content: string;
	format: DocumentFormat;
	isAgentDocument: boolean;
}

// [Gradient] Only complete markers are documents; legacy bodies remain raw markdown.
export function extractDocumentsFromMessage(content: string): AgentDocument[] {
	const documents: AgentDocument[] = [];
	const markers = /<details\b([^>]*)>([\s\S]*?)<\/details>/g;
	for (const match of (content ?? '').matchAll(markers)) {
		const attributes = Object.fromEntries(
			Array.from(match[1].matchAll(/([\w-]+)\s*=\s*(?:"([^"]*)"|'([^']*)')/g), (attr) => [
				attr[1],
				attr[2] ?? attr[3]
			])
		);
		if (attributes.type === 'document') {
			const isAgentDocument = attributes.format !== undefined;
			const body = match[2].replace(/^\s*<summary>[\s\S]*?<\/summary>/i, '');
			const documentContent = isAgentDocument
				? decode(body.replace(/^\r?\n/, '').replace(/\r?\n$/, ''))
				: body.trim();
			if (documentContent)
				documents.push({
					title: decode(attributes.title ?? ''),
					content: documentContent,
					format: attributes.format === 'html' ? 'html' : 'markdown',
					isAgentDocument
				});
		} else if (attributes.type === 'tool_calls' && attributes.name === 'write_document') {
			try {
				const args = JSON.parse(decode(attributes.arguments ?? ''));
				if (typeof args.markdown === 'string' && args.markdown.length > 0) {
					documents.push({
						title: args.title ?? '',
						content: args.markdown,
						format: 'markdown',
						isAgentDocument: false
					});
				}
			} catch {
				// Legacy tool arguments can be incomplete while streaming.
			}
		}
	}
	return documents;
}
