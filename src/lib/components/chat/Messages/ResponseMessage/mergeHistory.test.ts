import { describe, it, expect } from 'vitest';
import {
	detectMergeProtocol,
	mergeStatusAndReasoning,
	parseToolOffsets,
	type ReasoningItem,
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
		expect(out).toEqual([
			{ ...s[0], kind: 'status' },
			r[0]
		]);
	});

	it('places in-progress reasoning after status entries (the bug fixed 2026-05-13)', () => {
		// web_search is hidden=false, so even while done=false on the
		// reasoning side, the status_first branch is still taken. This pins
		// the fix from the 2026-05-13 SSE replay handoff: reasoning must
		// render below the status bullet during streaming, not above it.
		const s = [status('web_search', { done: false, hidden: false })];
		const r = [reasoning(0, '')];
		const out = mergeStatusAndReasoning(s, r, []);
		expect(out).toEqual([
			{ ...s[0], kind: 'status' },
			r[0]
		]);
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
		const s = [
			{ description: 'De gespreksgeschiedenis is te lang…', done: true } as StatusEntry
		];
		expect(detectMergeProtocol(s, [reasoning(0)], [])).toBe('reasoning_only');
	});

	it('returns status_first for empty status AND empty reasoning (output is empty either way)', () => {
		expect(detectMergeProtocol([], [], [])).toBe('status_first');
	});
});

// parseToolOffsets --------------------------------------------------------

describe('parseToolOffsets', () => {
	it('returns indices of every <details type="tool_calls"> marker', () => {
		const content = 'abc<details type="tool_calls" id="1">x</details>def<details type="tool_calls">y</details>';
		expect(parseToolOffsets(content)).toEqual([3, 51]);
	});

	it('returns [] when no markers are present', () => {
		expect(parseToolOffsets('plain content with <details type="reasoning">…</details>')).toEqual([]);
	});

	it('returns [] for empty content', () => {
		expect(parseToolOffsets('')).toEqual([]);
	});
});
