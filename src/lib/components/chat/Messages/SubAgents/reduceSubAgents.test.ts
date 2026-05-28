import { describe, it, expect } from 'vitest';
import { reduceSubAgents, TEXT_BUFFER_LIMIT_BYTES } from './reduceSubAgents';
import type {
	SubAgentEvent,
	SubagentStartEvent,
	SubagentTokenEvent,
	SubagentStepEvent,
	SubagentDoneEvent
} from '$lib/types/subagent';

const start = (
	agent_id: string,
	parallel_group_id: string,
	overrides: Partial<SubagentStartEvent> = {}
): SubagentStartEvent => ({
	phase: 'start',
	agent_id,
	agent_label: overrides.agent_label ?? `Label ${agent_id}`,
	capability_name: overrides.capability_name ?? `capability_${agent_id}`,
	parallel_group_id,
	started_at: overrides.started_at ?? 1000
});

const token = (agent_id: string, text_delta: string): SubagentTokenEvent => ({
	phase: 'token',
	agent_id,
	text_delta
});

const step = (
	agent_id: string,
	step_label: string,
	step_status: 'running' | 'done' = 'running'
): SubagentStepEvent => ({
	phase: 'step',
	agent_id,
	step_label,
	step_status
});

const done = (
	agent_id: string,
	overrides: Partial<SubagentDoneEvent> = {}
): SubagentDoneEvent => ({
	phase: 'done',
	agent_id,
	summary: overrides.summary ?? `summary for ${agent_id}`,
	ended_at: overrides.ended_at ?? 2000,
	ok: overrides.ok ?? true,
	error: overrides.error ?? null
});

describe('reduceSubAgents', () => {
	it('returns an empty list for no events', () => {
		expect(reduceSubAgents([])).toEqual([]);
	});

	it('single-card sequence: start → token → step → done collapses to one group', () => {
		const events: SubAgentEvent[] = [
			start('a1', 'g1'),
			token('a1', 'Hello '),
			token('a1', 'world'),
			step('a1', 'Searching knowledge graph'),
			done('a1', { summary: 'Found 3 facts' })
		];

		const groups = reduceSubAgents(events);

		expect(groups).toHaveLength(1);
		expect(groups[0].parallel_group_id).toBe('g1');
		expect(groups[0].cards).toHaveLength(1);

		const card = groups[0].cards[0];
		expect(card.agent_id).toBe('a1');
		expect(card.state).toBe('done');
		expect(card.text_buffer).toBe('Hello world');
		expect(card.step_label).toBe('Searching knowledge graph');
		expect(card.summary).toBe('Found 3 facts');
		expect(card.ended_at).toBe(2000);
		expect(card.error).toBeNull();
	});

	it('parallel-pair: two starts sharing parallel_group_id land in one group', () => {
		const events: SubAgentEvent[] = [
			start('a1', 'g1'),
			start('a2', 'g1'),
			token('a1', 'alpha'),
			token('a2', 'beta'),
			done('a1', { summary: 'done a1' }),
			done('a2', { summary: 'done a2' })
		];

		const groups = reduceSubAgents(events);

		expect(groups).toHaveLength(1);
		expect(groups[0].cards).toHaveLength(2);
		expect(groups[0].cards.map((c) => c.agent_id)).toEqual(['a1', 'a2']);
		expect(groups[0].cards[0].text_buffer).toBe('alpha');
		expect(groups[0].cards[1].text_buffer).toBe('beta');
		expect(groups[0].cards.every((c) => c.state === 'done')).toBe(true);
	});

	it('error-in-pair: one card transitions to error, sibling stays done', () => {
		const events: SubAgentEvent[] = [
			start('a1', 'g1'),
			start('a2', 'g1'),
			token('a1', 'progress'),
			token('a2', 'progress'),
			done('a1', { ok: false, error: 'timeout', summary: '' }),
			done('a2', { ok: true, summary: 'ok summary' })
		];

		const groups = reduceSubAgents(events);

		expect(groups).toHaveLength(1);
		const [a1, a2] = groups[0].cards;
		expect(a1.state).toBe('error');
		expect(a1.error).toBe('timeout');
		expect(a2.state).toBe('done');
		expect(a2.error).toBeNull();
	});

	it('late-arriving step: step after done still updates step_label', () => {
		const events: SubAgentEvent[] = [
			start('a1', 'g1'),
			token('a1', 'x'),
			done('a1'),
			step('a1', 'Cleanup', 'done')
		];

		const groups = reduceSubAgents(events);

		expect(groups[0].cards[0].step_label).toBe('Cleanup');
		expect(groups[0].cards[0].state).toBe('done'); // step does not regress state
	});

	it('groups split when parallel_group_id changes between starts', () => {
		const events: SubAgentEvent[] = [
			start('a1', 'g1'),
			done('a1'),
			start('a2', 'g2'),
			done('a2')
		];

		const groups = reduceSubAgents(events);

		expect(groups).toHaveLength(2);
		expect(groups[0].parallel_group_id).toBe('g1');
		expect(groups[0].cards.map((c) => c.agent_id)).toEqual(['a1']);
		expect(groups[1].parallel_group_id).toBe('g2');
		expect(groups[1].cards.map((c) => c.agent_id)).toEqual(['a2']);
	});

	it('first non-empty token transitions pending → running', () => {
		// Manual collapse/expand state: visual layer owns expansion; reducer
		// only owns the logical state machine. Verify it ticks correctly
		// from start → running on first non-empty token.
		const afterStart = reduceSubAgents([start('a1', 'g1')]);
		expect(afterStart[0].cards[0].state).toBe('pending');

		const afterEmptyToken = reduceSubAgents([start('a1', 'g1'), token('a1', '')]);
		expect(afterEmptyToken[0].cards[0].state).toBe('pending');

		const afterRealToken = reduceSubAgents([start('a1', 'g1'), token('a1', 'x')]);
		expect(afterRealToken[0].cards[0].state).toBe('running');
	});

	it('text_buffer caps at 8 KB, truncating oldest prefix', () => {
		const limit = TEXT_BUFFER_LIMIT_BYTES;
		// Pre-fill with limit chars of 'a', then push 5 'b's. The buffer
		// should keep the last `limit` chars: (limit - 5) 'a's + 'bbbbb'.
		const filler = 'a'.repeat(limit);
		const events: SubAgentEvent[] = [
			start('a1', 'g1'),
			token('a1', filler),
			token('a1', 'bbbbb')
		];

		const card = reduceSubAgents(events)[0].cards[0];
		expect(card.text_buffer.length).toBe(limit);
		expect(card.text_buffer.endsWith('bbbbb')).toBe(true);
		expect(card.text_buffer.startsWith('a')).toBe(true);
	});

	it('ignores token/step/done events with no preceding start', () => {
		const events: SubAgentEvent[] = [
			token('ghost', 'lost'),
			step('ghost', 'phantom'),
			done('ghost')
		];

		expect(reduceSubAgents(events)).toEqual([]);
	});
});
