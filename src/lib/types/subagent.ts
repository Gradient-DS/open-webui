/**
 * TypeScript types for the `event: subagent` SSE family dispatched by the
 * agent runner when a parent agent (e.g. the Leiden bezwaar parent) spawns
 * one or more SubAgents in parallel.
 *
 * The agent service emits four phases per SubAgent — `start`, `token`,
 * `step`, `done` — keyed by `agent_id` and grouped by `parallel_group_id`.
 * `Chat.svelte` appends every payload onto `message.subagents` verbatim;
 * `reduceSubAgents` folds the flat stream into per-group card view-models
 * for `SubAgentGroup.svelte` to render.
 */

export interface SubagentStartEvent {
	phase: 'start';
	agent_id: string;
	agent_label: string;
	capability_name: string;
	parallel_group_id: string;
	started_at: number;
}

export interface SubagentTokenEvent {
	phase: 'token';
	agent_id: string;
	text_delta: string;
}

export interface SubagentStepEvent {
	phase: 'step';
	agent_id: string;
	step_label: string;
	step_status: 'running' | 'done';
}

export interface SubagentDoneEvent {
	phase: 'done';
	agent_id: string;
	summary: string;
	ended_at: number;
	ok: boolean;
	error: string | null;
}

export type SubAgentEvent =
	| SubagentStartEvent
	| SubagentTokenEvent
	| SubagentStepEvent
	| SubagentDoneEvent;

export type SubAgentCardState = 'pending' | 'running' | 'done' | 'error';

export interface SubAgentCardVM {
	agent_id: string;
	agent_label: string;
	capability_name: string;
	parallel_group_id: string;
	state: SubAgentCardState;
	text_buffer: string;
	step_label: string;
	summary: string;
	started_at: number;
	ended_at: number | null;
	error: string | null;
}

export interface SubAgentGroupVM {
	parallel_group_id: string;
	cards: SubAgentCardVM[];
}
