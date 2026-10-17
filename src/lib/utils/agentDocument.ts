import { decode } from 'html-entities';

export type DocumentFormat = 'markdown' | 'html';

export interface AgentDocument {
	title: string;
	content: string;
	format: DocumentFormat;
	done: boolean;
	// An HTML document's page size (documentPages.PAGE_SIZES); A4 when absent or unknown.
	page?: string;
}

export const DOCUMENT_OPEN = /<document\b((?:[^>"']|"[^"]*"|'[^']*')*)>/;

function attributes(text: string): Record<string, string> {
	return Object.fromEntries(
		Array.from(text.matchAll(/([\w-]+)\s*=\s*(?:"([^"]*)"|'([^']*)')/g), (attr) => [
			attr[1],
			attr[2] ?? attr[3]
		])
	);
}

// The first closing tag ends a document, including one inside a Markdown code fence.
export function readDocument(
	content: string
): { document: AgentDocument; raw: string } | undefined {
	const open = DOCUMENT_OPEN.exec(content);
	if (!open || open.index !== 0) return;
	const attrs = attributes(open[1]);
	const close = content.indexOf('</document>', open[0].length);
	const done = close !== -1;
	return {
		raw: done ? content.slice(0, close + '</document>'.length) : content,
		document: {
			title: decode(attrs.title ?? ''),
			format: attrs.format === 'html' ? 'html' : 'markdown',
			content: content.slice(open[0].length, done ? close : undefined),
			done,
			...(attrs.page && { page: attrs.page })
		}
	};
}

// A model may write its document in its reasoning; the answer shows it, so the reasoning names it in one line.
export function labelDocuments(text: string, label: (title: string) => string): string {
	// GLM's tool-call token can stand in for the tag's `<`, as agent_v2 repairs in the answer.
	let rest = text.replace(/<tool_call>\s*<?(?=document\b)/g, '<');
	let labelled = '';
	let open: RegExpExecArray | null;
	while ((open = DOCUMENT_OPEN.exec(rest))) {
		const { document, raw } = readDocument(rest.slice(open.index))!;
		labelled += `${rest.slice(0, open.index)}\n\n_${label(document.title)}_\n\n`;
		rest = rest.slice(open.index + raw.length);
	}
	return labelled + rest;
}

export function extractDocumentsFromMessage(content: string): AgentDocument[] {
	const documents: AgentDocument[] = [];
	const markers = /<document\b|<details\b((?:[^>"']|"[^"]*"|'[^']*')*)>([\s\S]*?)<\/details>/g;
	let match: RegExpExecArray | null;
	while ((match = markers.exec(content ?? ''))) {
		if (match[0].startsWith('<document')) {
			const result = readDocument(content.slice(match.index));
			if (result) {
				documents.push(result.document);
				// Skip nested markup inside the raw document body.
				markers.lastIndex = match.index + result.raw.length;
			}
		} else {
			const attrs = attributes(match[1]);
			if (attrs.type === 'document') {
				const body = match[2].replace(/^\s*<summary>[\s\S]*?<\/summary>/i, '').trim();
				if (body)
					documents.push({
						title: decode(attrs.title ?? ''),
						content: body,
						format: 'markdown',
						done: attrs.done !== 'false'
					});
			} else if (attrs.type === 'tool_calls' && attrs.name === 'write_document') {
				try {
					const args = JSON.parse(decode(attrs.arguments ?? ''));
					if (typeof args.markdown === 'string' && args.markdown.length > 0) {
						documents.push({
							title: args.title ?? '',
							content: args.markdown,
							format: 'markdown',
							done: attrs.done === 'true'
						});
					}
				} catch {
					// Legacy tool arguments can be incomplete while streaming.
				}
			}
		}
	}
	return documents;
}
