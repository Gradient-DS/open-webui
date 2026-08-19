import { describe, it, expect } from 'vitest';
import {
	detectMergeProtocol,
	mergeStatusAndReasoning,
	parseToolOffsets,
	splitProseRuns,
	buildResponseBlocks,
	type ReasoningItem,
	type ResponseBlock,
	type StatusEntry
} from './mergeHistory';

// Fixture helpers ----------------------------------------------------------

const reasoning = (contentOffset: number, summary = `r${contentOffset}`): ReasoningItem => ({
	kind: 'reasoning',
	summary,
	body: '',
	attributes: {},
	contentOffset
});

const status = (action: string, extras: Partial<StatusEntry> = {}): StatusEntry => ({
	action,
	description: '',
	...extras
});

// Vanilla OWUI -------------------------------------------------------------

describe('mergeStatusAndReasoning — vanilla OWUI', () => {
	it('places done reasoning after status entries (knowledge_search complete)', () => {
		// knowledge_search emits hidden=true && done=true — not the pre-marker
		// signal — so the status_first branch is taken.
		const s = [status('knowledge_search', { done: true, hidden: true })];
		const r = [reasoning(0, 'Thought for 2s')];
		const out = mergeStatusAndReasoning(s, r, []);
		expect(out).toEqual([{ ...s[0], kind: 'status' }, r[0]]);
	});

	it('places in-progress reasoning after status entries (the bug fixed 2026-05-13)', () => {
		// web_search is hidden=false, so even while done=false on the
		// reasoning side, the status_first branch is still taken. This pins
		// the fix from the 2026-05-13 SSE replay handoff: reasoning must
		// render below the status bullet during streaming, not above it.
		const s = [status('web_search', { done: false, hidden: false })];
		const r = [reasoning(0, '')];
		const out = mergeStatusAndReasoning(s, r, []);
		expect(out).toEqual([{ ...s[0], kind: 'status' }, r[0]]);
	});
});

// ChatAgent ---------------------------------------------------------------

describe('mergeStatusAndReasoning — ChatAgent multi-tool', () => {
	it('interleaves reasoning between tool markers', () => {
		// Two tool_calls markers anchor the zip. Reasoning blocks fall
		// between them in stream position and end up between the
		// corresponding status entries. The trailing "summary" status has
		// no matching marker (toolOffsets[2] === undefined), so it stays
		// at the end with no reasoning ahead of it.
		const s = [
			status('tool_a', { done: true }),
			status('tool_b', { done: true }),
			status('summary', { done: true })
		];
		const r = [reasoning(50, 'first thought'), reasoning(150, 'second thought')];
		const toolOffsets = [100, 200];
		const out = mergeStatusAndReasoning(s, r, toolOffsets);
		expect(out).toEqual([
			r[0],
			{ ...s[0], kind: 'status' },
			r[1],
			{ ...s[1], kind: 'status' },
			{ ...s[2], kind: 'status' }
		]);
	});
});

describe('mergeStatusAndReasoning — ChatAgent pre-marker window', () => {
	it('places reasoning before tool-start status when hidden=true && done=false', () => {
		// The inline_tool_marker hasn't been emitted yet (toolOffsets === []),
		// but the agent has already published a hidden=true && done=false
		// tool-start status. The zip path runs, and with toolOffsets[0]
		// undefined the inner loop drains all reasoning ahead of the
		// status entry.
		const s = [status('soev_chat_manual', { done: false, hidden: true })];
		const r = [reasoning(10, 'thinking')];
		const out = mergeStatusAndReasoning(s, r, []);
		expect(out).toEqual([r[0], { ...s[0], kind: 'status' }]);
	});
});

// Reasoning-only ----------------------------------------------------------

describe('mergeStatusAndReasoning — reasoning only', () => {
	it('returns reasoning items unchanged when no status entries', () => {
		const r = [reasoning(0, 'a'), reasoning(10, 'b')];
		const out = mergeStatusAndReasoning([], r, []);
		expect(out).toEqual([r[0], r[1]]);
	});

	it('returns empty status (no reasoning, no status)', () => {
		expect(mergeStatusAndReasoning([], [], [])).toEqual([]);
	});

	it('returns tagged status entries when no reasoning is present', () => {
		const s = [status('web_search', { done: true })];
		const out = mergeStatusAndReasoning(s, [], []);
		expect(out).toEqual([{ ...s[0], kind: 'status' }]);
	});
});

// detectMergeProtocol -----------------------------------------------------

describe('detectMergeProtocol', () => {
	it('returns status_first when no tool markers and no pending agent tool', () => {
		// Vanilla OWUI happy path — e.g. web_search (hidden=false) or a
		// completed knowledge_search (hidden=true && done=true).
		const s = [status('web_search', { done: false, hidden: false })];
		expect(detectMergeProtocol(s, [reasoning(0)], [])).toBe('status_first');
	});

	it('returns positional when a hidden non-done tool-start status is in flight', () => {
		// ChatAgent pre-marker window: tool-start emitted (hidden=true &&
		// done=false) but the inline_tool_marker hasn't arrived yet.
		const s = [status('soev_chat_manual', { done: false, hidden: true })];
		expect(detectMergeProtocol(s, [reasoning(0)], [])).toBe('positional');
	});

	it('returns positional whenever tool markers are present', () => {
		// Once the inline_tool_marker shows up, toolOffsets is non-empty and
		// the zip path runs regardless of any hidden/done combination on
		// existing status entries.
		const s = [status('tool_a', { done: true })];
		expect(detectMergeProtocol(s, [reasoning(0)], [42])).toBe('positional');
	});

	it('returns status_first for hidden=true && done=true (completed knowledge_search)', () => {
		// Vanilla OWUI knowledge_search is hidden=true but done=true — must
		// NOT trip the pre-marker signal.
		const s = [status('knowledge_search', { done: true, hidden: true })];
		expect(detectMergeProtocol(s, [reasoning(0)], [])).toBe('status_first');
	});

	it('returns reasoning_only for empty status with reasoning present', () => {
		// No-tool turn (vanilla OWUI native LLM with no web_search /
		// knowledge_search firing, or an agent that chose to answer
		// directly). ResponseMessage.svelte mounts standalone
		// ReasoningBullets off this branch.
		expect(detectMergeProtocol([], [reasoning(0)], [])).toBe('reasoning_only');
	});

	it('returns reasoning_only when only non-tool status entries are present', () => {
		// Agent runner's budget-warning emission (``runner.py:725-726``)
		// publishes ``StatusUpdate(description=..., done=True)`` with no
		// ``action`` field. Without this branch the status entry traps the
		// reasoning inside an invisible dropdown (``hasToolCalls=false`` →
		// StatusHistory hides after done) and ``reasoning_only`` was never
		// reached because ``statusEntries.length > 0``. We must still mount
		// standalone ReasoningBullets in that case.
		const s = [{ description: 'De gespreksgeschiedenis is te lang…', done: true } as StatusEntry];
		expect(detectMergeProtocol(s, [reasoning(0)], [])).toBe('reasoning_only');
	});

	it('returns status_first for empty status AND empty reasoning (output is empty either way)', () => {
		expect(detectMergeProtocol([], [], [])).toBe('status_first');
	});
});

// parseToolOffsets --------------------------------------------------------

describe('parseToolOffsets', () => {
	it('returns indices of every <details type="tool_calls"> marker', () => {
		const content =
			'abc<details type="tool_calls" id="1">x</details>def<details type="tool_calls">y</details>';
		expect(parseToolOffsets(content)).toEqual([3, 51]);
	});

	it('returns [] when no markers are present', () => {
		expect(parseToolOffsets('plain content with <details type="reasoning">…</details>')).toEqual(
			[]
		);
	});

	it('returns [] for empty content', () => {
		expect(parseToolOffsets('')).toEqual([]);
	});
});

// Response blocks ----------------------------------------------------------

// The agent's inter-tool commentary lives in ``message.content`` alongside the
// ``<details>`` anchors, which MarkdownTokens deliberately suppresses. Before
// this, StatusHistory rendered every tool and thought in one dropdown and
// ContentRenderer rendered ALL of the prose underneath it, so commentary the
// model streamed between tool calls surfaced at the very bottom of the turn.
// ``buildResponseBlocks`` cuts the turn into alternating tool-group / prose
// blocks so each comment renders where it was actually streamed.

// ``at(-1)`` widens back to the union, so narrow explicitly rather than
// reaching for a non-null assertion in an assertion.
const contentText = (block?: ResponseBlock) =>
	block && block.kind === 'content' ? block.text : undefined;

const toolMarker = (name = 'web_search') =>
	`<details type="tool_calls" name="${name}">inner</details>`;
const reasoningMarker = (summary = 'Dacht 4 seconden') =>
	`<details type="reasoning" done="true">${summary}</details>`;

describe('splitProseRuns', () => {
	it('returns the whole content as one run when nothing was marked up', () => {
		expect(splitProseRuns('Just an answer.').map((r) => r.text)).toEqual(['Just an answer.']);
	});

	it('returns the prose between markers, in stream order', () => {
		const content = `Zoeken.${toolMarker()}Nog even nakijken.${toolMarker()}Klaar.`;
		expect(splitProseRuns(content).map((r) => r.text)).toEqual([
			'Zoeken.',
			'Nog even nakijken.',
			'Klaar.'
		]);
	});

	it('strips both anchor kinds so no text is ever rendered twice', () => {
		// The anchors are stream-position markers, not prose — StatusHistory is
		// the canonical surface for them. Leaving one in a run would duplicate
		// the tool/reasoning chrome inside the content flow.
		const content = `${reasoningMarker()}Ik ga zoeken.${toolMarker()}Gevonden.`;
		const runs = splitProseRuns(content);
		expect(runs.map((r) => r.text)).toEqual(['Ik ga zoeken.', 'Gevonden.']);
		expect(runs.some((r) => r.text.includes('<details'))).toBe(false);
	});

	it('reports each run offset so it can be ordered against the markers', () => {
		const content = `${toolMarker()}Klaar.`;
		const runs = splitProseRuns(content);
		expect(runs).toHaveLength(1);
		expect(runs[0].contentOffset).toBeGreaterThan(0);
	});

	it('drops whitespace-only runs', () => {
		const content = `${toolMarker()}\n\n${toolMarker()}Slot.`;
		expect(splitProseRuns(content).map((r) => r.text)).toEqual(['Slot.']);
	});
});

describe('buildResponseBlocks', () => {
	it('drops each comment below the thought and tool it followed', () => {
		const content = `${reasoningMarker()}Ik ga zoeken.${toolMarker()}Gevonden, nu verifiëren.${toolMarker()}Hier is de tabel.`;
		const offsets = parseToolOffsets(content);
		const s = [status('web_search'), status('web_search')];
		const r = [reasoning(0, 'Dacht 4 seconden')];
		const merged = mergeStatusAndReasoning(s, r, offsets);
		const blocks = buildResponseBlocks(merged, content, offsets);

		// Thought first, then what the model said about it, then the tool it
		// then ran — i.e. the comment sits between the thought and the call,
		// not at the bottom of the turn.
		expect(blocks.map((b) => b.kind)).toEqual([
			'status-group',
			'content',
			'status-group',
			'content',
			'status-group',
			'content'
		]);
		expect(blocks.filter((b) => b.kind === 'content').map((b) => b.text)).toEqual([
			'Ik ga zoeken.',
			'Gevonden, nu verifiëren.',
			'Hier is de tabel.'
		]);
	});

	it('makes the tail the last block, so the final answer stands alone', () => {
		const content = `Even kijken.${toolMarker()}Het antwoord is 42.`;
		const offsets = parseToolOffsets(content);
		const merged = mergeStatusAndReasoning([status('web_search')], [], offsets);
		const blocks = buildResponseBlocks(merged, content, offsets);
		expect(blocks.at(-1)?.kind).toBe('content');
		expect(contentText(blocks.at(-1))).toBe('Het antwoord is 42.');
	});

	it('emits no empty content block when the model went straight to a tool', () => {
		const content = `${toolMarker()}Klaar.`;
		const offsets = parseToolOffsets(content);
		const merged = mergeStatusAndReasoning([status('web_search')], [], offsets);
		expect(buildResponseBlocks(merged, content, offsets).map((b) => b.kind)).toEqual([
			'status-group',
			'content'
		]);
	});

	it('keeps consecutive tool calls in one group when nothing was said between', () => {
		const content = `${toolMarker()}${toolMarker()}Slot.`;
		const offsets = parseToolOffsets(content);
		const merged = mergeStatusAndReasoning(
			[status('web_search'), status('fetch_url')],
			[],
			offsets
		);
		const blocks = buildResponseBlocks(merged, content, offsets);
		expect(blocks.map((b) => b.kind)).toEqual(['status-group', 'content']);
		expect((blocks[0] as { items: unknown[] }).items).toHaveLength(2);
	});

	it('keeps a thought that follows a tool in that tool group, not in the answer', () => {
		const content = `Ik ga zoeken.${toolMarker()}${reasoningMarker('Dacht 9 seconden')}Klaar.`;
		const offsets = parseToolOffsets(content);
		const r = [reasoning(content.indexOf('<details type="reasoning"'), 'Dacht 9 seconden')];
		const merged = mergeStatusAndReasoning([status('web_search')], r, offsets);
		const blocks = buildResponseBlocks(merged, content, offsets);
		expect(blocks.map((b) => b.kind)).toEqual(['content', 'status-group', 'content']);
		expect((blocks[1] as { items: unknown[] }).items).toHaveLength(2);
		expect(contentText(blocks.at(-1))).toBe('Klaar.');
	});

	it('never loses status entries that have no marker of their own', () => {
		// statusHistory and tool markers are not guaranteed 1:1 — an entry can
		// arrive while its marker is still in flight. Every entry must still
		// land in a group, and the answer must still be the final block.
		const content = `${toolMarker()}Slot.`;
		const offsets = parseToolOffsets(content);
		const s = [status('web_search'), status('fetch_url'), status('fetch_url')];
		const merged = mergeStatusAndReasoning(s, [], offsets);
		const blocks = buildResponseBlocks(merged, content, offsets);
		const grouped = blocks
			.filter((b) => b.kind === 'status-group')
			.flatMap((b) => (b as { items: unknown[] }).items);
		expect(grouped).toHaveLength(3);
		expect(contentText(blocks.at(-1))).toBe('Slot.');
	});

	it('returns a single content block for a turn that called no tools', () => {
		const blocks = buildResponseBlocks([], 'Direct antwoord.', []);
		expect(blocks).toEqual([{ kind: 'content', text: 'Direct antwoord.', contentOffset: 0 }]);
	});
});
