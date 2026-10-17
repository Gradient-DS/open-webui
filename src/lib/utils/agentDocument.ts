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

// Where the opening tag that starts at `from` ends (its `>`), skipping quoted attribute values; -1 when it does
// not end. One pass, unlike a regular expression, which takes quadratic time on many unclosed tags.
function tagEnd(text: string, from: number): number {
	let quote = '';
	for (let index = from; index < text.length; index++) {
		const char = text[index];
		if (quote) {
			if (char === quote) quote = '';
		} else if (char === '"' || char === "'") quote = char;
		else if (char === '>') return index;
	}
	return -1;
}

// A model may write its document in its reasoning; the answer shows it, so the reasoning names it in one line.
export function labelDocuments(text: string, label: (title: string) => string): string {
	// GLM's tool-call token can stand in for the tag's `<`, as agent_v2 repairs in the answer.
	const rest = text.replace(/<tool_call>\s*<?(?=document\b)/g, '<');
	let labelled = '';
	let position = 0;
	let start: number;
	while ((start = rest.indexOf('<document', position)) !== -1) {
		const after = start + '<document'.length;
		if (/[\w-]/.test(rest[after] ?? '')) {
			labelled += rest.slice(position, after);
			position = after;
			continue;
		}
		const end = tagEnd(rest, after);
		if (end === -1) break;
		const title = decode(attributes(rest.slice(after, end)).title ?? '');
		const close = rest.indexOf('</document>', end);
		labelled += `${rest.slice(position, start)}\n\n_${label(title)}_\n\n`;
		position = close === -1 ? rest.length : close + '</document>'.length;
	}
	return labelled + rest.slice(position);
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
