<script>
	import { getContext } from 'svelte';
	const i18n = getContext('i18n');

	import StatusItem from './StatusHistory/StatusItem.svelte';
	import equal from 'fast-deep-equal';

	import ReasoningBullet from './StatusHistory/ReasoningBullet.svelte';
	import ChevronDown from '$lib/components/icons/ChevronDown.svelte';
	import ChevronUp from '$lib/components/icons/ChevronUp.svelte';

	// Heterogeneous list: each entry is either a status update (default) or a
	// reasoning bullet (when `kind === 'reasoning'`). ResponseMessage builds
	// this by merging `message.statusHistory` with reasoning details parsed
	// out of `message.content`.
	export let statusHistory = [];
	// [Gradient] Starts collapsed, as upstream does. The header line is the live
	// one and updates on every status event, so nothing is hidden while the turn
	// runs, and the chevron below makes the toggle discoverable — previously it
	// had no affordance at all.
	export let expand = false;
	// Set by ResponseMessage. When the parent message is done (SSE
	// [DONE] received), force any header in-progress indicators to
	// settled. Defensive belt-and-suspenders for the case where a
	// backend regression leaves a status entry with done=false at the
	// tail of statusHistory — the load-bearing fix is on the agent
	// side (closing done=true status for terminal tools), this guard
	// keeps the spinner from getting stuck if anything regresses.
	export let messageDone = false;
	let showHistory = expand;

	let history = [];

	// [Gradient] The promoted header is ALWAYS the newest entry and the list is
	// everything before it, so the expanded list stays one contiguous
	// chronological run. The previous rule promoted the last NON-reasoning entry,
	// which lifted a tool status out of the MIDDLE of the timeline and left the
	// reasoning bullets it separated adjacent to each other. That is what read as
	// "reasoning is not interleaved with the tool calls".
	$: status = history.at(-1) ?? null;
	$: historyItems = history.slice(0, -1);

	$: if (!equal(statusHistory, history)) {
		history = statusHistory;
	}

	const isReasoning = (item) => item?.kind === 'reasoning';
	// [Gradient] Stable keys: an unkeyed each re-created every row whenever the
	// list changed, which re-fired the .status-description fade on each status
	// event and read as flicker.
	const rowKey = (item, idx) =>
		item?.kind === 'reasoning'
			? `r-${item.contentOffset ?? idx}`
			: `s-${idx}-${item?.action ?? ''}`;
</script>

<!-- [Gradient] Visibility is decided one level up by ResponseMessage's
     shouldShowStatusHistory, whose OR lets live tool activity win over a hidden
     tail status. Upstream's unconditional inner gate defeated that OR. -->
{#if history && history.length > 0}
	<div class="text-[0.9375rem] flex flex-col w-full my-1">
		<button
			class="w-full text-gray-500 hover:text-gray-700 dark:hover:text-gray-300 transition"
			aria-label={$i18n.t('Toggle status history')}
			aria-expanded={showHistory}
			on:click={() => {
				showHistory = !showHistory;
			}}
		>
			<div class="flex items-start gap-2 min-w-0">
				<div class="flex-1 min-w-0">
					{#if isReasoning(status)}
						<ReasoningBullet
							id={`status-header`}
							summary={status.summary}
							body={status.body}
							attributes={messageDone && status?.attributes?.done !== 'true'
								? { ...(status.attributes ?? {}), done: 'true' }
								: (status.attributes ?? {})}
							asHeader={true}
						/>
					{:else}
						<StatusItem
							{status}
							done={messageDone || status?.done !== false}
							forceVisible={true}
							asHeader={true}
						/>
					{/if}
				</div>

				{#if historyItems.length > 0}
					<div class="flex shrink-0 self-center translate-y-[1px] text-gray-400 dark:text-gray-500">
						{#if showHistory}
							<ChevronUp strokeWidth="3.5" className="size-3" />
						{:else}
							<ChevronDown strokeWidth="3.5" className="size-3" />
						{/if}
					</div>
				{/if}
			</div>
		</button>

		{#if showHistory}
			<div class="flex flex-row">
				{#if historyItems.length > 0}
					<div class="w-full">
						{#each historyItems as item, idx (rowKey(item, idx))}
							<div class="flex items-stretch gap-2 mb-1">
								<div class=" ">
									<div class="pt-[0.625rem] px-1 mb-1.5">
										<span class="relative flex size-1.5 rounded-full justify-center items-center">
											<span
												class="relative inline-flex size-1.5 rounded-full bg-gray-400 dark:bg-gray-600"
											></span>
										</span>
									</div>
									{#if idx !== historyItems.length - 1}
										<div
											class="w-[0.03125rem] ml-[0.40625rem] h-[calc(100%-1.375rem)] bg-gray-200 dark:bg-gray-800"
										/>
									{/if}
								</div>

								{#if isReasoning(item)}
									<ReasoningBullet
										id={`status-${idx}`}
										summary={item.summary}
										body={item.body}
										attributes={item.attributes ?? {}}
									/>
								{:else}
									<StatusItem status={item} done={true} forceVisible={true} />
								{/if}
							</div>
						{/each}
					</div>
				{/if}
			</div>
		{/if}
	</div>
{/if}
