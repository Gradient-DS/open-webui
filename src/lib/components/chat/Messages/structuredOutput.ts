export type OutputContentPart = {
	type?: string;
	text?: unknown;
	[key: string]: unknown;
};

export type OutputItem = {
	type?: string;
	id?: string;
	call_id?: string;
	name?: string;
	status?: string;
	arguments?: unknown;
	content?: OutputContentPart[];
	summary?: OutputContentPart[];
	output?: OutputContentPart[];
	files?: unknown;
	embeds?: unknown;
	code?: string;
	lang?: string;
	duration?: number | string | null;
	action?: Record<string, unknown>;
	actions?: Array<Record<string, unknown>>;
	queries?: unknown[];
	[key: string]: unknown;
};

export type OutputDetailToken = {
	summary: string;
	text: string;
	attributes: {
		type: string;
		id?: string;
		name?: string;
		done?: string;
		duration?: string;
		arguments?: string;
		files?: string;
		embeds?: string;
		output?: string;
	};
};

export type OutputDisplayItem =
	| {
			type: 'message';
			id: string;
			text: string;
	  }
	| {
			// [Gradient] Document Writer: an open_webui:document output item,
			// pre-serialized to the <details type="document"> block the Markdown
			// path (MarkdownTokens → DocumentCard) already knows how to render.
			type: 'document';
			id: string;
			text: string;
	  }
	| {
			type: 'detail_single';
			id: string;
			token: OutputDetailToken;
	  }
	| {
			type: 'detail_group';
			id: string;
			tokens: OutputDetailToken[];
	  };

// [Gradient] Document Writer output item type (backend serialize_output()).
const DOCUMENT_OUTPUT_TYPE = 'open_webui:document';

const GROUPABLE_OUTPUT_TYPES = new Set([
	'reasoning',
	'function_call',
	'open_webui:code_interpreter',
	'web_search_call',
	'file_search_call',
	'computer_call'
]);

const OPENAI_TOOL_NAMES: Record<string, string> = {
	web_search_call: 'Web Search',
	file_search_call: 'File Search',
	computer_call: 'Computer Use'
};

function getTextFromParts(parts: OutputContentPart[] = []): string {
	return parts
		.map((part) => {
			if (part?.text === undefined || part?.text === null) {
				return '';
			}
			return typeof part.text === 'string' ? part.text : String(part.text);
		})
		.join('');
}

function stringifyAttribute(value: unknown): string {
	if (value === undefined || value === null) {
		return '';
	}
	if (typeof value === 'string') {
		return value;
	}
	try {
		return JSON.stringify(value);
	} catch {
		return String(value);
	}
}

function isDoneStatus(status?: string): boolean {
	return status === 'completed' || status === 'failed' || status === 'incomplete';
}

function getMessageText(item: OutputItem): string {
	return getTextFromParts(item.content ?? []);
}

function getReasoningText(item: OutputItem): string {
	const summary = Array.isArray(item.summary) && item.summary.length ? item.summary : null;
	return getTextFromParts(summary ?? item.content ?? []);
}

function getToolResultText(item?: OutputItem): string {
	return (item?.output ?? [])
		.filter((part) => part?.type !== 'input_image')
		.map((part) => {
			if (part?.text === undefined || part?.text === null) {
				return '';
			}
			return typeof part.text === 'string' ? part.text : String(part.text);
		})
		.join('');
}

function buildToolCallToken(item: OutputItem, toolOutputByCallId: Record<string, OutputItem>) {
	const callId = item.call_id ?? '';
	const resultItem = toolOutputByCallId[callId];
	const isDone = isDoneStatus(item.status) || !!resultItem;

	return {
		summary: isDone ? 'Tool Executed' : 'Executing...',
		text: getToolResultText(resultItem),
		attributes: {
			type: 'tool_calls',
			id: callId,
			name: item.name ?? '',
			done: isDone ? 'true' : 'false',
			arguments: stringifyAttribute(item.arguments ?? ''),
			files: stringifyAttribute(resultItem?.files),
			embeds: stringifyAttribute(resultItem?.embeds)
		}
	};
}

function buildReasoningToken(item: OutputItem, isLastItem: boolean) {
	const duration = item.duration ?? '';
	const isDone = isDoneStatus(item.status) || item.duration !== undefined || !isLastItem;
	const text = getReasoningText(item)
		.split('\n')
		.map((line) => (line.startsWith('>') ? line : `> ${line}`))
		.join('\n');

	return {
		summary: isDone ? `Thought for ${duration || 0} seconds` : 'Thinking...',
		text,
		attributes: {
			type: 'reasoning',
			done: isDone ? 'true' : 'false',
			duration: String(duration)
		}
	};
}

function buildCodeInterpreterToken(item: OutputItem, isLastItem: boolean) {
	const duration = item.duration ?? '';
	const isDone = isDoneStatus(item.status) || item.duration !== undefined || !isLastItem;
	const code = item.code ?? '';
	const lang = item.lang ?? 'python';

	return {
		summary: isDone ? 'Analyzed' : 'Analyzing...',
		text: code ? `\`\`\`${lang}\n${code}\n\`\`\`` : '',
		attributes: {
			type: 'code_interpreter',
			done: isDone ? 'true' : 'false',
			duration: String(duration),
			output: stringifyAttribute(item.output)
		}
	};
}

function getOpenAIToolSummary(item: OutputItem): string {
	if (item.type === 'web_search_call') {
		const action = item.action ?? {};
		const actionType = action.type;
		if (actionType === 'search') {
			const queries = Array.isArray(action.queries) ? action.queries : [];
			const query = typeof action.query === 'string' ? action.query : '';
			return queries.length ? `Search: ${queries.join(', ')}` : query ? `Search: ${query}` : '';
		}
		if (actionType === 'open_page' && typeof action.url === 'string') {
			return `Open page: ${action.url}`;
		}
		if (actionType === 'find_in_page' && typeof action.pattern === 'string') {
			return `Find in page: ${action.pattern}`;
		}
	}

	if (item.type === 'file_search_call') {
		const queries = item.queries ?? [];
		return queries.length ? `Queries: ${queries.join(', ')}` : '';
	}

	if (item.type === 'computer_call') {
		if (item.action?.type) {
			return `Action: ${item.action.type}`;
		}
		if (Array.isArray(item.actions) && item.actions.length) {
			return `Actions: ${item.actions.map((action) => action.type ?? '?').join(', ')}`;
		}
	}

	return '';
}

function buildOpenAIToolToken(item: OutputItem, isLastItem: boolean) {
	const isDone = isDoneStatus(item.status) || !isLastItem;
	return {
		summary: isDone ? 'Tool Executed' : 'Executing...',
		text: getOpenAIToolSummary(item),
		attributes: {
			type: 'tool_calls',
			id: item.id ?? '',
			name: OPENAI_TOOL_NAMES[item.type ?? ''] ?? item.type ?? '',
			done: isDone ? 'true' : 'false',
			arguments: ''
		}
	};
}

function buildDetailToken(
	item: OutputItem,
	isLastItem: boolean,
	toolOutputByCallId: Record<string, OutputItem>
): OutputDetailToken | null {
	if (item.type === 'function_call') {
		return buildToolCallToken(item, toolOutputByCallId);
	}
	if (item.type === 'reasoning') {
		return buildReasoningToken(item, isLastItem);
	}
	if (item.type === 'open_webui:code_interpreter') {
		return buildCodeInterpreterToken(item, isLastItem);
	}
	if (item.type && OPENAI_TOOL_NAMES[item.type]) {
		return buildOpenAIToolToken(item, isLastItem);
	}
	return null;
}

// [Gradient] A hidden tool call must not take its result's rendered content
// down with it: ToolCallDisplay is also the mount point for generative-UI
// iframe embeds and for image files returned by a tool. Those are content, not
// tool chrome, so a tool call carrying either stays visible.
function hasRenderableAttachments(token: OutputDetailToken): boolean {
	return [token.attributes.embeds, token.attributes.files].some((value) => {
		if (!value) {
			return false;
		}
		try {
			const parsed = JSON.parse(value);
			return Array.isArray(parsed) ? parsed.length > 0 : Boolean(parsed);
		} catch {
			return value.trim().length > 0;
		}
	});
}

export type BuildOutputDisplayItemsOptions = {
	// [Gradient] Set by StructuredOutputRenderer. StatusHistory is the fork's
	// canonical display surface for tool activity — the same reason
	// MarkdownTokens.svelte renders nothing for <details type="tool_calls">.
	// Without this, upstream's output-items path renders a ToolCallDisplay
	// dropdown per tool call that shows the tool name and arguments while the
	// response streams and then vanishes once the content path takes over.
	hideToolCalls?: boolean;
};

export function buildOutputDisplayItems(
	output: OutputItem[] = [],
	options: BuildOutputDisplayItemsOptions = {}
): OutputDisplayItem[] {
	const { hideToolCalls = false } = options;
	const displayItems: OutputDisplayItem[] = [];
	const currentDetailTokens: OutputDetailToken[] = [];
	const toolOutputByCallId: Record<string, OutputItem> = {};

	for (const item of output) {
		if (item?.type === 'function_call_output' && item.call_id) {
			toolOutputByCallId[item.call_id] = item;
		}
	}

	const flushDetails = () => {
		if (currentDetailTokens.length > 1) {
			displayItems.push({
				type: 'detail_group',
				id: `detail-group-${displayItems.length}`,
				tokens: [...currentDetailTokens]
			});
		} else if (currentDetailTokens.length === 1) {
			displayItems.push({
				type: 'detail_single',
				id: `detail-${displayItems.length}`,
				token: currentDetailTokens[0]
			});
		}
		currentDetailTokens.length = 0;
	};

	output.forEach((item, index) => {
		if (item?.type === 'function_call_output') {
			return;
		}

		if (item?.type && GROUPABLE_OUTPUT_TYPES.has(item.type)) {
			const token = buildDetailToken(item, index === output.length - 1, toolOutputByCallId);
			if (token) {
				const hidden =
					hideToolCalls &&
					token.attributes.type === 'tool_calls' &&
					!hasRenderableAttachments(token);
				if (!hidden) {
					currentDetailTokens.push(token);
				}
			}
			return;
		}

		if (item?.type === 'message') {
			const text = getMessageText(item);
			if (text.trim()) {
				flushDetails();
				displayItems.push({
					type: 'message',
					id: item.id ?? `message-${index}`,
					text
				});
			}
			return;
		}

		if (item?.type === DOCUMENT_OUTPUT_TYPE) {
			// [Gradient] Document Writer: getMessageText() is empty for document
			// items, so without this branch the fallback below dropped them and
			// the assistant bubble rendered nothing at all.
			flushDetails();
			displayItems.push({
				type: 'document',
				id: item.id ?? `document-${index}`,
				text: getDocumentText(item, index === output.length - 1)
			});
			return;
		}

		const fallbackText = getMessageText(item);
		if (fallbackText.trim()) {
			flushDetails();
			displayItems.push({
				type: 'message',
				id: item.id ?? `output-${index}`,
				text: fallbackText
			});
		}
	});

	flushDetails();
	return displayItems;
}

function escapeHtmlAttribute(value: string): string {
	// Mirror Python's html.escape(value, quote=True) so Chat.svelte's
	// decodeHtmlEntities() round-trips the title attribute correctly.
	return value
		.replace(/&/g, '&amp;')
		.replace(/</g, '&lt;')
		.replace(/>/g, '&gt;')
		.replace(/"/g, '&quot;')
		.replace(/'/g, '&#x27;');
}

function getDocumentTitle(item: OutputItem): string {
	if (typeof item.title === 'string' && item.title) {
		return item.title;
	}
	const attributes = item.attributes;
	if (attributes && typeof attributes === 'object') {
		const title = (attributes as Record<string, unknown>).title;
		if (typeof title === 'string') {
			return title;
		}
	}
	return '';
}

function getDocumentText(item: OutputItem, isLastItem: boolean): string {
	// [Gradient] Document Writer feature. Mirror backend serialize_output()'s
	// open_webui:document branch so that Chat.svelte getDocuments() and the
	// docx/pdf/txt/md exporter — both of which regex message.content for
	// <details type="document" ...> blocks — keep working now that documents
	// stream as output items instead of being baked into message content.
	const markdown = (typeof item.markdown === 'string' ? item.markdown : '').trim();
	const title = getDocumentTitle(item);
	const isDone = isDoneStatus(item.status) || item.duration !== undefined || !isLastItem;

	const titleAttr = title ? ` title="${escapeHtmlAttribute(title)}"` : '';
	const doneAttr = isDone ? 'true' : 'false';
	const summary = isDone ? 'Document' : 'Writing…';

	return `<details type="document" done="${doneAttr}"${titleAttr}>\n<summary>${summary}</summary>\n${markdown}\n</details>`;
}

export function hasDocumentOutput(output?: OutputItem[] | null): boolean {
	// [Gradient] Document Writer: message.content is persisted empty since
	// v0.10.2, so the document side panel can no longer rely on a content regex.
	return (output ?? []).some((item) => item?.type === DOCUMENT_OUTPUT_TYPE);
}

export function getOutputText(output?: OutputItem[] | null): string {
	const items = output ?? [];
	return items
		.map((item, index) => {
			if (item?.type === 'message') {
				return getMessageText(item);
			}
			if (item?.type === DOCUMENT_OUTPUT_TYPE) {
				return getDocumentText(item, index === items.length - 1);
			}
			return '';
		})
		.filter((text) => text.trim())
		.join('\n');
}

// [Gradient] Anchors for the StatusHistory reasoning-merge algorithm.
//
// Pre-v0.10.2, the backend serialized output items into message.content
// (middleware serialize_output): reasoning became <details type="reasoning">
// blocks and the agent's inline <details type="tool_calls"> markers stayed in
// the text, so ResponseMessage could regex-parse content to interleave
// reasoning bullets between tool statuses. Upstream v0.10.2 ships output
// items to the client directly and persists content empty, so the anchors
// are now derived from the output array itself. Reasoning items and tool
// markers share one monotonically increasing offset axis so mergePositional()
// in mergeHistory.ts can zip them against statusHistory exactly as before.

export type OutputReasoningAnchor = {
	kind: 'reasoning';
	summary: string;
	body: string;
	attributes: Record<string, string>;
	contentOffset: number;
};

export type OutputStreamAnchors = {
	reasoningItems: OutputReasoningAnchor[];
	toolOffsets: number[];
};

const TOOL_MARKER_RE = /<details type="tool_calls"[^>]*>/g;

export function getOutputStreamAnchors(output?: OutputItem[] | null): OutputStreamAnchors {
	const items = output ?? [];
	const reasoningItems: OutputReasoningAnchor[] = [];
	const toolOffsets: number[] = [];
	let offset = 0;

	items.forEach((item, index) => {
		if (item?.type === 'reasoning') {
			// Done inference mirrors the pre-v0.10.2 serializer: completed
			// status, a recorded duration, or any subsequent item means the
			// reasoning segment is closed.
			const isLastItem = index === items.length - 1;
			const isDone = isDoneStatus(item.status) || item.duration != null || !isLastItem;
			const attributes: Record<string, string> = {
				type: 'reasoning',
				done: isDone ? 'true' : 'false',
				duration: String(item.duration ?? 0)
			};
			const startedAt = Number(item.started_at);
			if (Number.isFinite(startedAt) && startedAt > 0) {
				// Unix seconds → milliseconds, matching the started_at attribute
				// the old serializer wrote (buildResponseParts sorts on ms).
				attributes.started_at = String(Math.round(startedAt * 1000));
			}
			reasoningItems.push({
				kind: 'reasoning',
				// Empty summary routes ReasoningBullet to its i18n-aware
				// "Thinking..." / "Thought for N seconds" labels.
				summary: '',
				body: getReasoningText(item)
					.split('\n')
					.map((line) => (line.startsWith('>') ? line : `> ${line}`))
					.join('\n'),
				attributes,
				contentOffset: offset
			});
			offset += 1;
			return;
		}

		if (item?.type === 'message') {
			const text = getMessageText(item);
			TOOL_MARKER_RE.lastIndex = 0;
			while (TOOL_MARKER_RE.exec(text) !== null) {
				toolOffsets.push(offset);
				offset += 1;
			}
		}
	});

	return { reasoningItems, toolOffsets };
}

export function replaceOutputMessageText(
	output: OutputItem[] = [],
	oldContent: string,
	newContent: string
): OutputItem[] {
	if (!oldContent) {
		return output;
	}

	let replaced = false;
	const nextOutput = output.map((item) => {
		if (replaced || item?.type !== 'message' || !Array.isArray(item.content)) {
			return item;
		}

		const partIndex = item.content.findIndex(
			(part) => typeof part.text === 'string' && part.text.includes(oldContent)
		);
		if (partIndex === -1) {
			return item;
		}

		replaced = true;
		const nextContent = [...item.content];
		const part = nextContent[partIndex];
		nextContent[partIndex] = {
			...part,
			text: (part.text as string).replace(oldContent, newContent)
		};

		return {
			...item,
			content: nextContent
		};
	});

	return replaced ? nextOutput : output;
}
