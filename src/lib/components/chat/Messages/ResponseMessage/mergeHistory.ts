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
	const hasPendingAgentTool = statusEntries.some((s) => s.hidden === true && s.done === false);
	if (hasPendingAgentTool) return 'positional';
	// Use the same "real tool action" criterion that ``ResponseMessage.svelte``'s
	// ``hasToolCalls`` does, rather than ``statusEntries.length === 0``. Non-tool
	// status entries (e.g. the agent runner's budget-warning ``StatusUpdate`` —
	// no ``action`` set) would otherwise trap reasoning inside an invisible
	// dropdown: ``hasToolCalls`` evaluates ``false`` so ``StatusHistory`` hides
	// after ``done``, and the standalone-reasoning mount never fires because
	// ``statusEntries.length > 0``.
	const hasToolActions = statusEntries.some(
		(s) => typeof s.action === 'string' && s.action !== 'reasoning_step'
	);
	if (!hasToolActions && reasoning.length > 0) return 'reasoning_only';
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

// Response blocks ----------------------------------------------------------
//
// The merged timeline above answers "in what order did the tools and thoughts
// happen". It does not answer "where does the model's own commentary go" —
// that prose lives in ``message.content``, interleaved with the very same
// ``<details>`` anchors, and ``MarkdownTokens`` suppresses those anchors so
// StatusHistory stays their only display surface.
//
// The consequence was that every comment the model streamed between tool
// calls rendered in one block at the BOTTOM of the turn, far from the call it
// was about. ``buildResponseBlocks`` cuts the turn into alternating
// tool-group / prose blocks, ordered by true content offset, so each comment
// renders where it was streamed and the final answer is the tail alone.

export type ContentBlock = {
	kind: 'content';
	text: string;
	contentOffset: number;
};

export type StatusGroupBlock = {
	kind: 'status-group';
	items: MergedItem[];
};

export type ResponseBlock = StatusGroupBlock | ContentBlock;

// Both anchor kinds, so a run of prose is never polluted by markup that
// StatusHistory already renders. Non-greedy and case-insensitive to match
// ``parseToolOffsets`` and the stripper in ``$lib/utils``.
const ANCHOR_DETAILS_RE = /<details\s+type="(?:tool_calls|reasoning)"[^>]*>[\s\S]*?<\/details>/gi;

export function splitProseRuns(content: string): ContentBlock[] {
	const runs: ContentBlock[] = [];
	const push = (raw: string, start: number) => {
		const text = raw.trim();
		// Whitespace between two adjacent anchors is not a comment; emitting it
		// would split one tool group into two for no visible reason.
		if (text !== '') runs.push({ kind: 'content', text, contentOffset: start });
	};

	let last = 0;
	let m: RegExpExecArray | null;
	ANCHOR_DETAILS_RE.lastIndex = 0;
	while ((m = ANCHOR_DETAILS_RE.exec(content)) !== null) {
		if (m.index > last) push(content.slice(last, m.index), last);
		last = m.index + m[0].length;
	}
	if (last < content.length) push(content.slice(last), last);
	return runs;
}

export function buildResponseBlocks(
	merged: MergedItem[],
	content: string,
	toolOffsets: number[]
): ResponseBlock[] {
	const prose = splitProseRuns(content);
	if (merged.length === 0) return prose;

	// Position every timeline item on the same axis as the prose: a reasoning
	// block already knows its offset, and the k-th status entry sits at the
	// k-th tool marker. Status entries and markers are NOT guaranteed 1:1 (an
	// entry can arrive while its marker is still in flight), so a surplus
	// entry pins to the last known marker — that keeps it in the final tool
	// group instead of stranding it after the answer.
	const lastToolOffset = toolOffsets.length > 0 ? toolOffsets[toolOffsets.length - 1] : 0;
	let toolIdx = 0;
	const positioned = merged.map((item) => {
		if (item.kind === 'reasoning') return { item, offset: item.contentOffset };
		const offset = toolOffsets[toolIdx] ?? lastToolOffset;
		toolIdx++;
		return { item, offset };
	});

	// Anchors before prose on a tie: a comment always describes the tool or
	// thought that produced it, so it belongs below, never above.
	const ordered = [
		...positioned.map((p) => ({ ...p, isProse: false as const })),
		...prose.map((p) => ({ item: p, offset: p.contentOffset, isProse: true as const }))
	].sort((a, b) => a.offset - b.offset || Number(a.isProse) - Number(b.isProse));

	const blocks: ResponseBlock[] = [];
	let group: MergedItem[] = [];
	const flush = () => {
		if (group.length > 0) {
			blocks.push({ kind: 'status-group', items: group });
			group = [];
		}
	};

	for (const entry of ordered) {
		if (entry.isProse) {
			flush();
			blocks.push(entry.item as ContentBlock);
		} else {
			group.push(entry.item as MergedItem);
		}
	}
	flush();
	return blocks;
}
