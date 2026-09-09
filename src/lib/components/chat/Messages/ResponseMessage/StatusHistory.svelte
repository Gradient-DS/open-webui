<script>
	import { getContext } from 'svelte';
	const i18n = getContext('i18n');

	import StatusItem from './StatusHistory/StatusItem.svelte';
	import equal from 'fast-deep-equal';

	import ReasoningBullet from './StatusHistory/ReasoningBullet.svelte';

	// Heterogeneous list: each entry is either a status update (default) or a
	// reasoning bullet (when `kind === 'reasoning'`). ResponseMessage builds
	// this by merging `message.statusHistory` with reasoning details parsed
	// out of `message.content`.
	export let statusHistory = [];
	// Default-open so users see the streaming progress and the final bullet
	// list without an extra click. The legacy reactive that bound this to a
	// rarely-used `expand` prop made the dropdown effectively unopenable in
	// Svelte 5 legacy mode — toggling via the header button is restored, just
	// starting open instead of closed.
	export let expand = true;
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
	let status = null;
	// [Gradient] Index of the entry promoted to the collapsed header, so the
	// expanded list can omit it instead of repeating it as its own bullet.
	let statusIndex = -1;

	$: if (history && history.length > 0) {
		// Prefer the last entry as the dropdown header BUT skip past a
		// completed reasoning bullet — "X bronnen opgehaald" is more
		// informative than "Dacht N seconden" once thinking has finished.
		// While reasoning is in_progress (``attributes.done !== 'true'``),
		// keep it as header so the spinner/shimmer signals to the user
		// that the model is still thinking. ChatAgent flows are unaffected
		// because the last entry is always the post-loop summary status
		// (not a reasoning bullet).
		const last = history.at(-1);
		const lastIsDoneReasoning = last?.kind === 'reasoning' && last?.attributes?.done === 'true';
		if (lastIsDoneReasoning) {
			let lastNonReasoning = null;
			let lastNonReasoningIndex = -1;
			for (let i = history.length - 1; i >= 0; i--) {
				if (history[i]?.kind !== 'reasoning') {
					lastNonReasoning = history[i];
					lastNonReasoningIndex = i;
					break;
				}
			}
			status = lastNonReasoning ?? last;
			statusIndex = lastNonReasoning ? lastNonReasoningIndex : history.length - 1;
		} else {
			status = last;
			statusIndex = history.length - 1;
		}
	}

	$: if (!equal(statusHistory, history)) {
		history = statusHistory;
	}

	// [Gradient] The header already renders `status`; showing it again as the
	// last bullet made every tool turn read "1 tool called in Ns" twice.
	$: historyItems = history.filter((_, idx) => idx !== statusIndex);

	const isReasoning = (item) => item?.kind === 'reasoning';
</script>

<!-- [Gradient] Visibility is decided one level up by ResponseMessage's
     shouldShowStatusHistory, whose OR lets live tool activity win over a hidden
     tail status. Upstream's unconditional inner gate defeated that OR. -->
{#if history && history.length > 0}
	<div class="text-[0.9375rem] flex flex-col w-full">
		<button
			class="w-full"
			aria-label={$i18n.t('Toggle status history')}
			aria-expanded={showHistory}
			on:click={() => {
				showHistory = !showHistory;
			}}
		>
			<div class="flex items-start gap-2">
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
						asHeader={history.length > 1}
					/>
				{/if}
			</div>
		</button>

		{#if showHistory}
			<div class="flex flex-row">
				{#if historyItems.length > 0}
					<div class="w-full">
						{#each historyItems as item, idx}
							<div class="flex items-stretch gap-2 mb-1">
								<div class=" ">
									<div class="pt-3 px-1 mb-1.5">
										<span class="relative flex size-1.5 rounded-full justify-center items-center">
											<span
												class="relative inline-flex size-1.5 rounded-full bg-gray-500 dark:bg-gray-400"
											></span>
										</span>
									</div>
									{#if idx !== historyItems.length - 1}
										<div
											class="w-[0.03125rem] ml-[0.40625rem] h-[calc(100%-14px)] bg-gray-300 dark:bg-gray-700"
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
