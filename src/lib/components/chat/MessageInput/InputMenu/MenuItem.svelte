<script lang="ts">
	// [Gradient] One row of the unified "+" menu: icon, label, optional submenu chevron,
	// switch or tool state, and the pin in the right-hand column.
	import type { Placement } from 'tippy.js';
	import type { ToolState } from '$lib/utils/toolState';

	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import Switch from '$lib/components/common/Switch.svelte';
	import ChevronRight from '$lib/components/icons/ChevronRight.svelte';
	import PinButton from './PinButton.svelte';
	import ToolStateLabel from './ToolStateLabel.svelte';

	export let label: string;
	export let onClick: (e: MouseEvent) => void;
	export let pinId: string | null = null;
	export let tooltip = '';
	export let tooltipPlacement: Placement = 'top';
	/** Rendered at half opacity and reported as aria-disabled; onClick still decides. */
	export let disabled = false;
	export let submenu = false;
	export let count: number | null = null;
	/** Shows a switch reflecting this state when not null. */
	export let toggle: boolean | null = null;
	/** Shows the tool's state as text when not null; the row's click cycles it. */
	export let toolState: ToolState | null = null;
	export let ariaLabel: string | undefined = undefined;
</script>

<Tooltip content={tooltip} placement={tooltipPlacement} className="w-full">
	<button
		type="button"
		class="flex w-full gap-2 items-center h-[1.6875rem] px-2 text-[0.8125rem] font-normal text-left select-none cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800/60 rounded-xl"
		class:opacity-50={disabled}
		aria-disabled={disabled}
		aria-pressed={toggle ?? undefined}
		aria-label={ariaLabel}
		on:click={onClick}
	>
		<div class="shrink-0 flex items-center justify-center size-3.5">
			<slot name="icon" />
		</div>

		<div class="flex-1 min-w-0 truncate">
			{label}
			{#if count !== null}
				<span class="ml-0.5 text-gray-500">{count}</span>
			{/if}
		</div>

		<slot name="actions" />

		{#if submenu}
			<div class="shrink-0 text-gray-500">
				<ChevronRight />
			</div>
		{/if}

		{#if toggle !== null}
			<div class="shrink-0" inert>
				<Switch state={toggle} />
			</div>
		{/if}

		{#if toolState !== null}
			<ToolStateLabel state={toolState} />
		{/if}

		{#if pinId}
			<PinButton itemId={pinId} />
		{/if}
	</button>
</Tooltip>
