// Pure helpers for merging streaming status entries and reasoning blocks
// for the response-message timeline. Kept out of ``ResponseMessage.svelte`` so
// the protocol decisions can be unit-tested without mounting a Svelte tree.
//
// See ``thoughts/shared/plans/2026-05-13-status-merge-protocol-refactor.md``
// for the protocol matrix this module implements.

export type StatusEntry = {
	kind?: 'status';
	action: string;
	description?: string;
	done?: boolean;
	hidden?: boolean;
	urls?: string[];
	query?: string;
	[key: string]: unknown;
};

export type ReasoningItem = {
	kind: 'reasoning';
	summary: string;
	body: string;
	attributes: Record<string, string>;
	contentOffset: number;
};

export type TaggedStatus = StatusEntry & { kind: 'status' };
export type MergedItem = TaggedStatus | ReasoningItem;

// Which merge strategy applies to the current turn. The decision is the
// single source of truth for "how do we render this": the dispatcher in
// ``mergeStatusAndReasoning`` selects a strategy off this enum, and (after
// Phase 3) the standalone-reasoning mount in ``ResponseMessage.svelte`` will
// branch off it too.
export type MergeProtocol =
	// ChatAgent: ``<details type="tool_calls">`` marker(s) emitted, or a
	// ``hidden=true && done=false`` tool-start status entry is in flight
	// (pre-marker window). Reasoning is interleaved between tool markers.
	| 'positional'
	// Vanilla OWUI / non-ChatAgent: no marker, no pending agent tool.
	// Reasoning is appended after the status entries so the dropdown reads
	// top-to-bottom in chronological order.
	| 'status_first'
	// No status entries this turn — the model chose to answer without
	// tool calls. ``ResponseMessage.svelte`` mounts the reasoning blocks
	// as standalone expanders instead of the StatusHistory dropdown.
	| 'reasoning_only';

export function parseToolOffsets(content: string): number[] {
	const offsets: number[] = [];
	const re = /<details type="tool_calls"[^>]*>/g;
	let m: RegExpExecArray | null;
	while ((m = re.exec(content)) !== null) {
		offsets.push(m.index);
	}
	return offsets;
}

export function detectMergeProtocol(
	statusEntries: StatusEntry[],
	reasoning: ReasoningItem[],
	toolOffsets: number[]
): MergeProtocol {
	if (toolOffsets.length > 0) return 'positional';
	// ChatAgent emits ``hidden=true && done=false`` on tool-start status
	// entries (``_dispatch_tool_status`` in ``agents/flows/core/agent.py``).
	// When we see one, we're in the agent's pre-marker window — the LLM may
	// have already emitted a content chunk that prematurely closed the
	// reasoning, but the inline_tool_marker is in flight. Vanilla OWUI's
	// ``knowledge_search`` is ``hidden=true`` but ``done=true`` (one-shot,
	// no marker follows), and its ``web_search`` emits ``hidden=false``, so
	// neither flips this signal.
	const hasPendingAgentTool = statusEntries.some(
		(s) => s.hidden === true && s.done === false
	);
	if (hasPendingAgentTool) return 'positional';
	if (statusEntries.length === 0 && reasoning.length > 0) return 'reasoning_only';
	return 'status_first';
}

function mergePositional(
	status: TaggedStatus[],
	reasoning: ReasoningItem[],
	toolOffsets: number[]
): MergedItem[] {
	// Walk reasoning and status entries, interleaving by position. For each
	// reasoning block, count how many tool_calls markers appear before it —
	// that index in `status` is where it belongs (inserted BEFORE the next
	// status entry, since reasoning precedes the next tool call in stream
	// order).
	const merged: MergedItem[] = [];
	let reasoningIdx = 0;
	for (let i = 0; i < status.length; i++) {
		const toolOffset = toolOffsets[i];
		while (
			reasoningIdx < reasoning.length &&
			(toolOffset === undefined || reasoning[reasoningIdx].contentOffset < toolOffset)
		) {
			merged.push(reasoning[reasoningIdx]);
			reasoningIdx++;
		}
		merged.push(status[i]);
	}
	while (reasoningIdx < reasoning.length) {
		merged.push(reasoning[reasoningIdx]);
		reasoningIdx++;
	}
	return merged;
}

function mergeStatusFirst(status: TaggedStatus[], reasoning: ReasoningItem[]): MergedItem[] {
	return [...status, ...reasoning];
}

export function mergeStatusAndReasoning(
	statusEntries: StatusEntry[],
	reasoning: ReasoningItem[],
	toolOffsets: number[]
): MergedItem[] {
	const status: TaggedStatus[] = statusEntries.map((s) => ({ ...s, kind: 'status' as const }));
	if (reasoning.length === 0) return status;
	const protocol = detectMergeProtocol(statusEntries, reasoning, toolOffsets);
	switch (protocol) {
		case 'positional':
			return mergePositional(status, reasoning, toolOffsets);
		case 'reasoning_only':
		// ``reasoning_only`` falls through: the markup mounts standalone
		// ``ReasoningBullet``s off the protocol enum directly, but the merged
		// list still needs to be well-formed (``mergeStatusFirst`` with empty
		// status returns the reasoning items unchanged).
		// eslint-disable-next-line no-fallthrough
		case 'status_first':
			return mergeStatusFirst(status, reasoning);
	}
}
