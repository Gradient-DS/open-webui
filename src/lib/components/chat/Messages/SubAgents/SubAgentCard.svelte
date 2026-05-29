<script lang="ts">
	import { onDestroy, tick } from 'svelte';
	import { slide } from 'svelte/transition';

	import Spinner from '$lib/components/common/Spinner.svelte';
	import ChevronDown from '$lib/components/icons/ChevronDown.svelte';
	import ChevronUp from '$lib/components/icons/ChevronUp.svelte';
	import StatusHistory from '$lib/components/chat/Messages/ResponseMessage/StatusHistory.svelte';
	import Citations from '$lib/components/chat/Messages/Citations.svelte';

	import type { SubAgentCardVM } from '$lib/types/subagent';

	// Slice 3.3 lifecycle:
	//   start              → pending  (spinner + label, no body)
	//   first token        → running  (expanded, streaming buffer)
	//   done(ok=true)      → done     (auto-collapse after 2 s, click to re-expand)
	//   done(ok=false)     → error    (stays expanded, no auto-collapse)
	export let card: SubAgentCardVM;

	const AUTO_COLLAPSE_DELAY_MS = 2000;

	// `expanded` is driven by state + user action. Pending: collapsed
	// (nothing to show). Running: expanded. Done: expanded for 2 s then
	// auto-collapses. Error: stays expanded. User clicks override.
	let expanded = false;
	let userOverrideExpanded: boolean | null = null;
	let collapseTimer: ReturnType<typeof setTimeout> | null = null;

	let reasoningOpen = false;

	let bodyEl: HTMLDivElement | undefined;
	let autoScroll = true;

	let reasoningBodyEl: HTMLDivElement | undefined;
	let reasoningAutoScroll = true;

	$: state = card.state;

	// Auto-expand on first transition into running. Reactively cancel any
	// pending auto-collapse if state regresses out of done (defensive).
	$: handleStateChange(state);

	function handleStateChange(s: typeof state) {
		if (s === 'pending') {
			expanded = userOverrideExpanded ?? false;
			return;
		}
		if (s === 'running') {
			expanded = userOverrideExpanded ?? true;
			return;
		}
		if (s === 'error') {
			expanded = userOverrideExpanded ?? true;
			return;
		}
		// done: start expanded, auto-collapse after delay unless user overrode.
		if (s === 'done') {
			if (userOverrideExpanded !== null) {
				expanded = userOverrideExpanded;
				return;
			}
			expanded = true;
			if (collapseTimer) clearTimeout(collapseTimer);
			collapseTimer = setTimeout(() => {
				if (userOverrideExpanded === null) {
					expanded = false;
				}
			}, AUTO_COLLAPSE_DELAY_MS);
		}
	}

	function toggleExpanded() {
		userOverrideExpanded = !expanded;
		expanded = !expanded;
		if (collapseTimer) {
			clearTimeout(collapseTimer);
			collapseTimer = null;
		}
	}

	// Auto-scroll the streamed body while running, mirroring the chat
	// container pattern at Messages.svelte:469-476: track whether the user
	// is near the bottom; if so, scroll to bottom on every text update; if
	// the user scrolls up, pause auto-scroll until they return.
	function onBodyScroll() {
		if (!bodyEl) return;
		const distanceFromBottom = bodyEl.scrollHeight - bodyEl.scrollTop - bodyEl.clientHeight;
		autoScroll = distanceFromBottom <= 32;
	}

	// React to text_buffer changes — Svelte 4-style reactive statement
	// triggers the after-update scroll.
	$: if (card.text_buffer || expanded) {
		void scheduleScroll();
	}

	// Same auto-scroll-to-bottom behaviour for the streamed reasoning pane,
	// which is also capped at a fixed height (``max-h-48``) inside the
	// expanded card body so the whole card doesn't grow tall every time the
	// SubAgent's LLM emits another reasoning chunk.
	$: if (card.reasoning_buffer && reasoningOpen) {
		void scheduleReasoningScroll();
	}

	function onReasoningScroll() {
		if (!reasoningBodyEl) return;
		const distanceFromBottom =
			reasoningBodyEl.scrollHeight - reasoningBodyEl.scrollTop - reasoningBodyEl.clientHeight;
		reasoningAutoScroll = distanceFromBottom <= 32;
	}

	async function scheduleReasoningScroll() {
		if (!reasoningAutoScroll) return;
		await tick();
		if (reasoningBodyEl && reasoningAutoScroll) {
			reasoningBodyEl.scrollTop = reasoningBodyEl.scrollHeight;
		}
	}

	async function scheduleScroll() {
		if (!autoScroll) return;
		await tick();
		if (bodyEl && autoScroll) {
			bodyEl.scrollTop = bodyEl.scrollHeight;
		}
	}

	onDestroy(() => {
		if (collapseTimer) clearTimeout(collapseTimer);
	});
</script>

<div
	class="w-full rounded-2xl border border-gray-100 dark:border-gray-800 bg-gray-50/60 dark:bg-gray-900/40 px-3 py-2 text-sm"
	data-state={state}
	data-agent-id={card.agent_id}
>
	<button
		type="button"
		class="w-full flex items-center gap-2 text-left"
		on:click={toggleExpanded}
		aria-expanded={expanded}
	>
		<span class="shrink-0">
			{#if state === 'pending' || state === 'running'}
				<Spinner className="size-3.5" />
			{:else if state === 'error'}
				<span class="inline-block size-2.5 rounded-full bg-red-500"></span>
			{:else}
				<span class="inline-block size-2.5 rounded-full bg-emerald-500"></span>
			{/if}
		</span>

		<span class="flex-1 truncate font-medium text-gray-700 dark:text-gray-200">
			{card.agent_label}
		</span>

		{#if card.step_label && (state === 'running' || state === 'pending')}
			<span class="hidden sm:inline truncate text-xs text-gray-500 dark:text-gray-400 max-w-[40%]">
				{card.step_label}
			</span>
		{:else if state === 'done' && card.summary}
			<span class="hidden sm:inline truncate text-xs text-gray-500 dark:text-gray-400 max-w-[40%]">
				{card.summary}
			</span>
		{/if}

		<span class="shrink-0 text-gray-400 dark:text-gray-500">
			{#if expanded}
				<ChevronUp className="size-3.5" />
			{:else}
				<ChevronDown className="size-3.5" />
			{/if}
		</span>
	</button>

	{#if expanded}
		<div transition:slide={{ duration: 150 }} class="mt-2">
			{#if state === 'error'}
				<div class="rounded-lg bg-red-50 dark:bg-red-950/40 px-2 py-1.5 text-xs text-red-700 dark:text-red-300">
					{card.error ?? 'SubAgent failed'}
				</div>
			{/if}

			{#if card.reasoning_buffer}
				<details class="mt-1 text-xs" bind:open={reasoningOpen}>
					<summary class="cursor-pointer text-gray-500 dark:text-gray-400 select-none">
						Thinking ({card.reasoning_total_chars.toLocaleString()} chars)
					</summary>
					<div
						bind:this={reasoningBodyEl}
						on:scroll={onReasoningScroll}
						class="mt-1 max-h-48 overflow-y-auto whitespace-pre-wrap break-words text-xs text-gray-500 dark:text-gray-400 font-mono leading-relaxed"
					>
						{card.reasoning_buffer}
					</div>
				</details>
			{/if}

			{#if card.status_history.length > 0}
				<div class="mt-1 text-xs">
					<StatusHistory
						statusHistory={card.status_history}
						expand={true}
						messageDone={state === 'done' || state === 'error'}
					/>
				</div>
			{/if}

			{#if card.sources.length > 0}
				<div class="mt-1 text-xs">
					<Citations
						id={card.agent_id}
						chatId=""
						sources={card.sources}
						readOnly={true}
						messageDone={state === 'done' || state === 'error'}
					/>
				</div>
			{/if}

			{#if card.text_buffer}
				<div
					bind:this={bodyEl}
					on:scroll={onBodyScroll}
					class="mt-1 max-h-48 overflow-y-auto whitespace-pre-wrap break-words text-xs text-gray-600 dark:text-gray-300 font-mono leading-relaxed"
				>
					{card.text_buffer}
				</div>
			{/if}

			{#if state === 'done' && card.summary && !card.text_buffer}
				<div class="text-xs text-gray-600 dark:text-gray-300">
					{card.summary}
				</div>
			{/if}
		</div>
	{/if}
</div>
