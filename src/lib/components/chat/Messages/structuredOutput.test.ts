import { describe, it, expect } from 'vitest';

import {
	buildOutputDisplayItems,
	getOutputProseRuns,
	getOutputStreamAnchors,
	hasDocumentOutput,
	type OutputItem
} from './structuredOutput';
import { buildResponseBlocks, mergeStatusAndReasoning } from './ResponseMessage/mergeHistory';

// Fixture helpers ----------------------------------------------------------
// Shapes mirror what backend middleware persists on message.output since
// upstream v0.10.2 (Responses-style items; see the chat-table dump in the
// 2026-07-22 status/reasoning interleave regression investigation).

const reasoningItem = (text: string, overrides: Partial<OutputItem> = {}): OutputItem => ({
	type: 'reasoning',
	id: `r_${text.length}`,
	status: 'completed',
	start_tag: '<think>',
	end_tag: '</think>',
	attributes: { type: 'reasoning_content' },
	content: [{ type: 'output_text', text }],
	summary: null as unknown as undefined,
	started_at: 1784728000.0,
	ended_at: 1784728004.0,
	duration: 4,
	...overrides
});

const messageItem = (text: string): OutputItem => ({
	type: 'message',
	id: `msg_${text.length}`,
	status: 'completed',
	role: 'assistant',
	content: [{ type: 'output_text', text }]
});

const toolMarker = (name: string): string =>
	`\n\n<details type="tool_calls" done="true" name="${name}">\n<summary>Running ${name}...</summary>\n</details>\n\n`;

// getOutputStreamAnchors ----------------------------------------------------

describe('getOutputStreamAnchors', () => {
	it('returns empty anchors for missing or empty output', () => {
		expect(getOutputStreamAnchors(undefined)).toEqual({ reasoningItems: [], toolOffsets: [] });
		expect(getOutputStreamAnchors([])).toEqual({ reasoningItems: [], toolOffsets: [] });
	});

	it('extracts reasoning bullets with done/duration/started_at attributes', () => {
		const anchors = getOutputStreamAnchors([reasoningItem('deep thoughts'), messageItem('done')]);

		expect(anchors.reasoningItems).toHaveLength(1);
		const item = anchors.reasoningItems[0];
		expect(item.kind).toBe('reasoning');
		expect(item.body).toBe('> deep thoughts');
		expect(item.attributes.done).toBe('true');
		expect(item.attributes.duration).toBe('4');
		// Unix seconds → milliseconds, matching the pre-v0.10.2 serializer.
		expect(item.attributes.started_at).toBe('1784728000000');
	});

	it('marks a trailing in-progress reasoning item as not done', () => {
		const anchors = getOutputStreamAnchors([
			reasoningItem('still thinking', {
				status: 'in_progress',
				duration: undefined,
				ended_at: undefined
			})
		]);

		expect(anchors.reasoningItems[0].attributes.done).toBe('false');
	});

	it('infers done for a non-trailing in-progress reasoning item', () => {
		const anchors = getOutputStreamAnchors([
			reasoningItem('interrupted', { status: 'in_progress', duration: undefined }),
			messageItem('answer')
		]);

		expect(anchors.reasoningItems[0].attributes.done).toBe('true');
	});

	it('assigns interleavable offsets across reasoning items and tool markers', () => {
		// The regression scenario: reasoning → tool → reasoning → tool →
		// reasoning → answer, exactly as the agent streamed it.
		const anchors = getOutputStreamAnchors([
			reasoningItem('r0'),
			messageItem(toolMarker('list_documents')),
			reasoningItem('r1'),
			messageItem(toolMarker('read_document')),
			reasoningItem('r2'),
			messageItem('The final answer.')
		]);

		expect(anchors.reasoningItems.map((r) => r.contentOffset)).toEqual([0, 2, 4]);
		expect(anchors.toolOffsets).toEqual([1, 3]);
	});

	it('counts multiple markers inside a single message item', () => {
		const anchors = getOutputStreamAnchors([
			reasoningItem('r0'),
			messageItem(toolMarker('tool_a') + toolMarker('tool_b')),
			reasoningItem('r1')
		]);

		expect(anchors.reasoningItems.map((r) => r.contentOffset)).toEqual([0, 3]);
		expect(anchors.toolOffsets).toEqual([1, 2]);
	});
});

// Document Writer display items -------------------------------------------
// Shape copied from the NEO deployment's persisted chat rows: the final save
// writes content "" and an output array whose document item carries the
// markdown; before the fix these items were dropped by the fallback branch
// (getMessageText() is '' for them) and the bubble rendered empty.

const documentItem = (overrides: Partial<OutputItem> = {}): OutputItem => ({
	type: 'open_webui:document',
	status: 'completed',
	title: 'T',
	markdown: '# H\n\nbody',
	...overrides
});

describe('buildOutputDisplayItems: documents', () => {
	it('emits a completed document display item from the persisted shape', () => {
		const items = buildOutputDisplayItems([
			{ type: 'message', content: [{ type: 'output_text', text: 'intro\n\n' }] },
			documentItem(),
			reasoningItem('afterthought')
		]);

		expect(items.map((item) => item.type)).toEqual(['message', 'document', 'detail_single']);
		const doc = items[1] as { type: 'document'; text: string };
		expect(doc.text).toBe(
			'<details type="document" done="true" title="T">\n<summary>Document</summary>\n# H\n\nbody\n</details>'
		);
	});

	it('renders an in-progress trailing document as the Writing… form', () => {
		const items = buildOutputDisplayItems([
			{ type: 'message', content: [{ type: 'output_text', text: 'intro\n\n' }] },
			documentItem({ status: 'in_progress', markdown: '# H' })
		]);

		const doc = items[1] as { type: 'document'; text: string };
		expect(doc.text).toBe(
			'<details type="document" done="false" title="T">\n<summary>Writing…</summary>\n# H\n</details>'
		);
	});

	it('escapes the title so Chat.svelte decodeHtmlEntities round-trips it', () => {
		const items = buildOutputDisplayItems([documentItem({ title: 'A "B" & C' })]);
		const doc = items[0] as { type: 'document'; text: string };
		expect(doc.text).toContain('title="A &quot;B&quot; &amp; C"');
	});

	it('detects document output items for the side panel', () => {
		expect(hasDocumentOutput([messageItem('hi')])).toBe(false);
		expect(hasDocumentOutput([messageItem('hi'), documentItem()])).toBe(true);
		expect(hasDocumentOutput(undefined)).toBe(false);
	});
});

// Tool-call display items --------------------------------------------------
// StatusHistory is the fork's canonical surface for tool activity (see the
// carve-outs in MarkdownTokens.svelte and StructuredOutputRenderer.svelte), so
// the renderer asks for tool calls to be hidden. Generative-UI tool results —
// iframe embeds and image files — are real content and must survive the
// carve-out.

const functionCallItem = (name: string, overrides: Partial<OutputItem> = {}): OutputItem => ({
	type: 'function_call',
	id: `fc_${name}`,
	call_id: `call_${name}`,
	name,
	status: 'completed',
	arguments: '{"query":"subsidie"}',
	...overrides
});

const functionCallOutputItem = (name: string, overrides: Partial<OutputItem> = {}): OutputItem => ({
	type: 'function_call_output',
	call_id: `call_${name}`,
	output: [{ type: 'output_text', text: 'result' }],
	...overrides
});

describe('buildOutputDisplayItems: tool calls', () => {
	it('renders tool calls by default', () => {
		const items = buildOutputDisplayItems([
			functionCallItem('search_knowledge'),
			functionCallOutputItem('search_knowledge'),
			messageItem('The answer.')
		]);

		expect(items.map((item) => item.type)).toEqual(['detail_single', 'message']);
	});

	it('hides tool calls when the renderer asks for it', () => {
		const items = buildOutputDisplayItems(
			[
				functionCallItem('search_knowledge'),
				functionCallOutputItem('search_knowledge'),
				messageItem('The answer.')
			],
			{ hideToolCalls: true }
		);

		expect(items.map((item) => item.type)).toEqual(['message']);
	});

	it('hides OpenAI built-in tool calls too', () => {
		const items = buildOutputDisplayItems(
			[
				{ type: 'web_search_call', id: 'ws_1', status: 'completed', action: { type: 'search' } },
				messageItem('The answer.')
			],
			{ hideToolCalls: true }
		);

		expect(items.map((item) => item.type)).toEqual(['message']);
	});

	it('emits no empty detail group when every grouped tool call is hidden', () => {
		const items = buildOutputDisplayItems(
			[
				messageItem('intro'),
				functionCallItem('tool_a'),
				functionCallItem('tool_b'),
				functionCallOutputItem('tool_a'),
				functionCallOutputItem('tool_b'),
				messageItem('outro')
			],
			{ hideToolCalls: true }
		);

		expect(items.map((item) => item.type)).toEqual(['message', 'message']);
	});

	it('keeps a tool call whose result carries generative-UI embeds', () => {
		const items = buildOutputDisplayItems(
			[
				functionCallItem('present_ui'),
				functionCallOutputItem('present_ui', { embeds: ['https://example.test/widget'] }),
				messageItem('The answer.')
			],
			{ hideToolCalls: true }
		);

		expect(items.map((item) => item.type)).toEqual(['detail_single', 'message']);
	});

	it('keeps a tool call whose result carries files', () => {
		const items = buildOutputDisplayItems(
			[
				functionCallItem('make_chart'),
				functionCallOutputItem('make_chart', { files: ['data:image/png;base64,AAAA'] }),
				messageItem('The answer.')
			],
			{ hideToolCalls: true }
		);

		expect(items.map((item) => item.type)).toEqual(['detail_single', 'message']);
	});

	it('hides a tool call whose result carries empty embed and file lists', () => {
		const items = buildOutputDisplayItems(
			[
				functionCallItem('search_knowledge'),
				functionCallOutputItem('search_knowledge', { embeds: [], files: [] }),
				messageItem('The answer.')
			],
			{ hideToolCalls: true }
		);

		expect(items.map((item) => item.type)).toEqual(['message']);
	});

	it('leaves reasoning and code interpreter items alone', () => {
		const items = buildOutputDisplayItems(
			[
				reasoningItem('thinking'),
				{ type: 'open_webui:code_interpreter', status: 'completed', code: 'print(1)' },
				functionCallItem('search_knowledge'),
				messageItem('The answer.')
			],
			{ hideToolCalls: true }
		);

		expect(items.map((item) => item.type)).toEqual(['detail_group', 'message']);
		const group = items[0] as {
			type: 'detail_group';
			tokens: Array<{ attributes: { type: string } }>;
		};
		expect(group.tokens.map((token) => token.attributes.type)).toEqual([
			'reasoning',
			'code_interpreter'
		]);
	});
});

// Regression: 2026-07-22 upstream v0.10.2 merge --------------------------
// Upstream's output-items pipeline leaves message.content empty, so anchors
// must come from output for the positional merge to interleave reasoning
// between the tool statuses they separated (instead of statuses collapsing
// into one block with all reasoning trailing after).

describe('output anchors + positional merge (v0.10.2 interleave regression)', () => {
	it('restores chronological status/reasoning interleaving from output items', () => {
		const output = [
			reasoningItem('r0'),
			messageItem(toolMarker('list_documents')),
			reasoningItem('r1'),
			messageItem(toolMarker('read_document')),
			reasoningItem('r2'),
			messageItem('The final answer.')
		];
		const statusEntries = [
			{ action: 'list_documents', description: 'Listing…', done: false, hidden: true },
			{ action: 'read_document', description: 'Reading…', done: false, hidden: true },
			{ action: 'summary', description: '2 tools called in 11 seconds', done: true }
		];

		const { reasoningItems, toolOffsets } = getOutputStreamAnchors(output);
		const merged = mergeStatusAndReasoning(statusEntries, reasoningItems, toolOffsets);

		expect(
			merged.map((m) => (m.kind === 'reasoning' ? `reasoning:${m.body}` : `status:${m.action}`))
		).toEqual([
			'reasoning:> r0',
			'status:list_documents',
			'reasoning:> r1',
			'status:read_document',
			'reasoning:> r2',
			'status:summary'
		]);
	});
});

// Regression: 2026-08-20 inter-tool commentary on the output path ----------
// On deployments where upstream v0.10.2 streams ``message.output``, the
// anchors are ORDINAL (0, 1, 2, …) while ``message.content`` is the flattened
// ``getOutputText`` string. Cutting that string by character offset against
// ordinal tool offsets clumped every anchor before almost every prose run, so
// the commentary collapsed to the bottom of the turn. ``getOutputProseRuns``
// emits the prose on the same ordinal axis (anchor k sits at k; the prose
// streamed just before it at k - 0.5), which is what lets
// ``buildResponseBlocks`` interleave them exactly.

describe('getOutputProseRuns', () => {
	it('returns no runs for missing or empty output', () => {
		expect(getOutputProseRuns(undefined)).toEqual([]);
		expect(getOutputProseRuns([])).toEqual([]);
	});

	it('positions prose runs between the ordinals of the anchors around them', () => {
		const output = [
			messageItem(`Ik ga zoeken.${toolMarker('web_search')}Eerste tussenstap.`),
			reasoningItem('even nadenken'),
			messageItem(`${toolMarker('fetch')}Het eindantwoord met tabel.`)
		];

		// Anchor axis for reference: tool@0, reasoning@1, tool@2.
		expect(getOutputStreamAnchors(output).toolOffsets).toEqual([0, 2]);

		expect(getOutputProseRuns(output)).toEqual([
			{ kind: 'content', text: 'Ik ga zoeken.', contentOffset: -0.5 },
			{ kind: 'content', text: 'Eerste tussenstap.', contentOffset: 0.5 },
			{ kind: 'content', text: 'Het eindantwoord met tabel.', contentOffset: 2.5 }
		]);
	});

	it('treats an in-flight (unclosed) tool marker as anchor, not prose', () => {
		const output = [
			messageItem(
				'Intro.\n\n<details type="tool_calls" done="false" name="x">\n<summary>Running…</summary>'
			)
		];

		expect(getOutputStreamAnchors(output).toolOffsets).toEqual([0]);
		expect(getOutputProseRuns(output)).toEqual([
			{ kind: 'content', text: 'Intro.', contentOffset: -0.5 }
		]);
	});

	it('keeps document items in the prose stream as serialized details blocks', () => {
		const output = [
			messageItem(`Zoeken.${toolMarker('web_search')}`),
			documentItem(),
			messageItem('Klaar.')
		];

		const runs = getOutputProseRuns(output);
		expect(runs).toHaveLength(2);
		expect(runs[0]).toEqual({ kind: 'content', text: 'Zoeken.', contentOffset: -0.5 });
		expect(runs[1].contentOffset).toBe(0.5);
		expect(runs[1].text).toBe(
			'<details type="document" done="true" title="T">\n<summary>Document</summary>\n# H\n\nbody\n</details>\n\nKlaar.'
		);
	});
});

describe('output prose runs + buildResponseBlocks (commentary-at-bottom regression)', () => {
	it('interleaves tool groups and commentary in stream order, answer as tail', () => {
		const output = [
			messageItem(`${toolMarker('web_search')}Nu ga ik de pagina ophalen.`),
			reasoningItem('r1'),
			messageItem(`${toolMarker('fetch')}Het eindantwoord.`)
		];
		const statusEntries = [
			{ action: 'web_search', description: 'Zoeken…', done: true, hidden: true },
			{ action: 'fetch', description: 'Ophalen…', done: true, hidden: true },
			{ action: 'summary', description: '2 tools called', done: true }
		];

		const { reasoningItems, toolOffsets } = getOutputStreamAnchors(output);
		const merged = mergeStatusAndReasoning(statusEntries, reasoningItems, toolOffsets);
		const blocks = buildResponseBlocks(merged, getOutputProseRuns(output), toolOffsets);

		expect(
			blocks.map((b) =>
				b.kind === 'content'
					? `content:${b.text}`
					: `group:${b.items.map((i) => (i.kind === 'reasoning' ? 'r' : i.action)).join('+')}`
			)
		).toEqual([
			'group:web_search',
			'content:Nu ga ik de pagina ophalen.',
			'group:r+fetch+summary',
			'content:Het eindantwoord.'
		]);
	});
});
