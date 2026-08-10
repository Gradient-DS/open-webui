import { describe, it, expect } from 'vitest';

import {
	buildOutputDisplayItems,
	getOutputStreamAnchors,
	hasDocumentOutput,
	type OutputItem
} from './structuredOutput';
import { mergeStatusAndReasoning } from './ResponseMessage/mergeHistory';

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
