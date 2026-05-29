/**
 * Pure reducer that folds the flat list of `event: subagent` SSE payloads
 * appended by `Chat.svelte` into an ordered list of group view-models
 * rendered by `SubAgentGroup.svelte`.
 *
 * Grouping rule: two `start` events with the same `parallel_group_id` land
 * in the same group when no intervening start with a different group id has
 * opened a new group. In practice the agent runner dispatches all `start`
 * events for a parallel batch before the first `token` arrives, so the
 * stream is well-formed; the reducer is defensive against reordering only
 * to the extent that an out-of-order start with the current group id is
 * still merged in.
 *
 * Per-card state machine: `pending` → first non-empty token → `running` →
 * `done(ok=true)` → `done` (or `done(ok=false)` → `error`). Token / step
 * events that arrive after `done` still update `text_buffer` and
 * `step_label`, but never regress the terminal state.
 *
 * Buffer cap: `text_buffer` is capped at TEXT_BUFFER_LIMIT_BYTES chars.
 * On overflow the oldest prefix is dropped to fit. Streaming UIs only
 * render the tail, and the parent agent receives the full text by other
 * means — this is purely a render budget.
 */

import type {
	SubAgentCardVM,
	SubAgentEvent,
	SubAgentGroupVM,
	SubagentDoneEvent,
	SubagentReasoningEvent,
	SubagentSourceEvent,
	SubagentStartEvent,
	SubagentStatusEvent,
	SubagentStepEvent,
	SubagentTokenEvent
} from '$lib/types/subagent';

export const TEXT_BUFFER_LIMIT_BYTES = 8 * 1024;
export const REASONING_BUFFER_LIMIT_BYTES = 8 * 1024;
export const STATUS_HISTORY_LIMIT = 64;
export const SOURCES_LIMIT = 32;

export function reduceSubAgents(events: SubAgentEvent[]): SubAgentGroupVM[] {
	const groups: SubAgentGroupVM[] = [];
	const cardIndex = new Map<string, SubAgentCardVM>();
	let currentGroup: SubAgentGroupVM | null = null;

	for (const event of events) {
		if (event.phase === 'start') {
			currentGroup = handleStart(event, groups, cardIndex, currentGroup);
			continue;
		}

		const card = cardIndex.get(event.agent_id);
		if (!card) continue; // ignore stray events for unknown agents

		if (event.phase === 'token') {
			applyToken(card, event);
		} else if (event.phase === 'step') {
			applyStep(card, event);
		} else if (event.phase === 'done') {
			applyDone(card, event);
		} else if (event.phase === 'reasoning') {
			applyReasoning(card, event);
		} else if (event.phase === 'status') {
			applyStatus(card, event);
		} else if (event.phase === 'source') {
			applySource(card, event);
		}
	}

	return groups;
}

function handleStart(
	event: SubagentStartEvent,
	groups: SubAgentGroupVM[],
	cardIndex: Map<string, SubAgentCardVM>,
	currentGroup: SubAgentGroupVM | null
): SubAgentGroupVM {
	const card: SubAgentCardVM = {
		agent_id: event.agent_id,
		agent_label: event.agent_label,
		capability_name: event.capability_name,
		parallel_group_id: event.parallel_group_id,
		state: 'pending',
		text_buffer: '',
		reasoning_buffer: '',
		status_history: [],
		sources: [],
		step_label: '',
		summary: '',
		started_at: event.started_at,
		ended_at: null,
		error: null
	};
	cardIndex.set(event.agent_id, card);

	if (currentGroup && currentGroup.parallel_group_id === event.parallel_group_id) {
		currentGroup.cards.push(card);
		return currentGroup;
	}

	const fresh: SubAgentGroupVM = {
		parallel_group_id: event.parallel_group_id,
		cards: [card]
	};
	groups.push(fresh);
	return fresh;
}

function applyToken(card: SubAgentCardVM, event: SubagentTokenEvent): void {
	if (event.text_delta.length === 0) return;
	const next = card.text_buffer + event.text_delta;
	card.text_buffer =
		next.length > TEXT_BUFFER_LIMIT_BYTES ? next.slice(next.length - TEXT_BUFFER_LIMIT_BYTES) : next;
	if (card.state === 'pending') {
		card.state = 'running';
	}
}

function applyStep(card: SubAgentCardVM, event: SubagentStepEvent): void {
	card.step_label = event.step_label;
}

function applyDone(card: SubAgentCardVM, event: SubagentDoneEvent): void {
	card.state = event.ok ? 'done' : 'error';
	card.summary = event.summary;
	card.ended_at = event.ended_at;
	card.error = event.error;
}

function applyReasoning(card: SubAgentCardVM, event: SubagentReasoningEvent): void {
	if (event.text_delta.length === 0) return;
	const next = card.reasoning_buffer + event.text_delta;
	card.reasoning_buffer =
		next.length > REASONING_BUFFER_LIMIT_BYTES
			? next.slice(next.length - REASONING_BUFFER_LIMIT_BYTES)
			: next;
}

function applyStatus(card: SubAgentCardVM, event: SubagentStatusEvent): void {
	card.status_history.push(event.status);
	if (card.status_history.length > STATUS_HISTORY_LIMIT) {
		card.status_history = card.status_history.slice(-STATUS_HISTORY_LIMIT);
	}
}

function applySource(card: SubAgentCardVM, event: SubagentSourceEvent): void {
	card.sources.push(event.source);
	if (card.sources.length > SOURCES_LIMIT) {
		card.sources = card.sources.slice(-SOURCES_LIMIT);
	}
}
