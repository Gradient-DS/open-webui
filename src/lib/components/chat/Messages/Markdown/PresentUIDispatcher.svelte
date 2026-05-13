<!--
	Generative-UI dispatcher — renders the per-component widget for each
	`message.uiBlocks` entry surfaced by the agent service's
	`event: present_ui` SSE custom events.

	Adding a new component = add an entry to COMPONENTS keyed by the
	registered name (matching the backend ComponentRegistry). Unknown
	names render nothing — that's the forward-compat path when a newer
	backend emits a component the frontend hasn't shipped yet.
-->
<script lang="ts">
	import { getContext } from 'svelte';
	import { choiceBlockRegistry, submitPromptSignal } from '$lib/stores';
	import ChoiceBlock from './ChoiceBlock.svelte';

	const i18n: any = getContext('i18n');

	export let blocks: Array<{ id: string; name: string; props: any }> = [];
	export let messageId: string;
	export let messageDone: boolean = false;

	const COMPONENTS: Record<string, any> = {
		choice: ChoiceBlock
	};

	// When this message contains 2+ ``choice`` blocks we render a SINGLE
	// shared Confirm button + progress indicator at the bottom, instead
	// of one Confirm per block (which read as "do I have to click 5
	// Confirms?"). Per-block ChoiceBlocks suppress their own Confirm in
	// that case (see ChoiceBlock.svelte: ``hasSiblings``).
	$: choiceBlocks = blocks.filter((b) => b.name === 'choice');
	$: isMultiChoice = choiceBlocks.length > 1;

	// Subscribe to the per-message registry the ChoiceBlocks write to.
	$: scope = $choiceBlockRegistry[messageId] ?? {};
	$: registered = Object.keys(scope);
	$: answered = registered.filter((bid) => !!scope[bid]?.selection);
	$: allAnswered = registered.length > 0 && answered.length === registered.length;
	$: anyConfirmed = registered.some((bid) => scope[bid]?.answered);

	// Mirror of ChoiceBlock's ``confirmAll`` for the batch case. Kept
	// here so a single button at the dispatcher level can submit all
	// answers atomically without coupling to any one block's lifecycle.
	function confirmAll() {
		const snapshot = { ...($choiceBlockRegistry[messageId] ?? {}) };
		const blockIds = Object.keys(snapshot);

		// Build the combined reply: one line per question, using
		// ``field: value`` when ``field`` was set so each answer is
		// self-identifying.
		const lines = blockIds.map((bid) => {
			const entry = snapshot[bid];
			const value = entry?.selection ?? '';
			if (entry?.field) return `${entry.field}: ${value}`;
			return value;
		});
		const combined = lines.join('\n');

		// Persist answered state for every block so a refresh restores it.
		blockIds.forEach((bid) => {
			const entry = snapshot[bid];
			if (!entry?.selection) return;
			try {
				localStorage.setItem(`ui:choice:${messageId}:${bid}`, entry.selection);
			} catch {
				/* localStorage unavailable */
			}
		});

		// Mark all blocks answered in the registry — each block mirrors
		// this back into local state and disables its UI.
		choiceBlockRegistry.update((map) => {
			const next = { ...(map[messageId] ?? {}) };
			Object.keys(next).forEach((bid) => {
				const e = next[bid];
				if (e?.selection) next[bid] = { ...e, answered: true };
			});
			return { ...map, [messageId]: next };
		});

		submitPromptSignal.set({ text: combined, ts: Date.now() });
	}
</script>

{#each blocks as block (block.id)}
	{@const Component = COMPONENTS[block.name]}
	{#if Component}
		<svelte:component
			this={Component}
			props={block.props}
			blockId={block.id}
			{messageId}
			{messageDone}
		/>
	{/if}
{/each}

{#if isMultiChoice && !anyConfirmed}
	<div
		class="mt-3 flex items-center gap-3 px-4 py-2.5 rounded-xl bg-gray-50 dark:bg-gray-900/60 border border-gray-100 dark:border-gray-800"
	>
		<button
			type="button"
			class="px-4 py-1.5 rounded-xl text-sm font-medium bg-black text-white dark:bg-white dark:text-black
				transition hover:opacity-90
				disabled:opacity-40 disabled:cursor-not-allowed"
			disabled={!messageDone || !allAnswered}
			on:click={confirmAll}
		>
			{$i18n?.t ? $i18n.t('Confirm all') : 'Confirm all'}
		</button>
		<span class="text-xs text-gray-500 dark:text-gray-400">
			{$i18n?.t
				? $i18n.t('{{answered}} of {{total}} answered', {
						answered: answered.length,
						total: registered.length
					})
				: `${answered.length} of ${registered.length} answered`}
		</span>
	</div>
{/if}
