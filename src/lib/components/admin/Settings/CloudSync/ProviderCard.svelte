<script lang="ts">
	import { getContext, createEventDispatcher } from 'svelte';
	import type { ComponentType, SvelteComponent } from 'svelte';
	import type { Writable } from 'svelte/store';
	import type { i18n as i18nType } from 'i18next';

	import dayjs from '$lib/dayjs';
	import relativeTime from 'dayjs/plugin/relativeTime';
	dayjs.extend(relativeTime);

	import Badge from '$lib/components/common/Badge.svelte';
	import ChevronDown from '$lib/components/icons/ChevronDown.svelte';
	import type { CloudSyncProviderStatus } from './types';

	const i18n = getContext<Writable<i18nType>>('i18n');
	const dispatch = createEventDispatcher<{ toggle: { expanded: boolean } }>();

	// Provider name (already translated / a plain label).
	export let name = '';
	// Provider icon component, rendered `size-5`.
	export let icon: ComponentType<SvelteComponent<{ className?: string }>>;
	// Whether the provider integration is enabled — drives the header badge.
	export let enabled = false;
	// Cross-provider status (from the cloud-sync status endpoint). Null until
	// loaded.
	export let status: CloudSyncProviderStatus | null = null;

	// Accordion state — bindable so the parent panel keeps one-open-at-a-time
	// (or all-collapsed) state. Toggling also dispatches `toggle`.
	export let expanded = false;

	const toggle = () => {
		expanded = !expanded;
		dispatch('toggle', { expanded });
	};

	// Status line: "{kb} KBs · {files} files · last sync {when} · {status}".
	// Suspended KBs flip the line to a warning variant. No KBs → "Not in use".
	$: hasSuspended = (status?.suspended_count ?? 0) > 0;
	$: notInUse = status !== null && status.kb_count === 0;

	$: lastSyncLabel = status?.last_sync_at
		? dayjs(status.last_sync_at * 1000).fromNow()
		: $i18n.t('Never');

	$: statusWord = status?.status === 'syncing' ? $i18n.t('Syncing') : $i18n.t('Idle');
</script>

<div
	class="rounded-xl border border-gray-100 dark:border-gray-850 hover:bg-gray-50/50 dark:hover:bg-gray-850/30 transition"
>
	<button
		type="button"
		class="w-full flex items-center gap-3 px-3.5 py-3 text-left"
		on:click={toggle}
		aria-expanded={expanded}
	>
		<svelte:component this={icon} className="size-5 shrink-0" />

		<div class="flex flex-col min-w-0 flex-1">
			<div class="font-medium">{name}</div>
			<div
				class="text-xs {hasSuspended
					? 'text-amber-600 dark:text-amber-500'
					: 'text-gray-500'} truncate"
			>
				{#if status === null}
					&nbsp;
				{:else if notInUse}
					{$i18n.t('Not in use')}
				{:else}
					{status.kb_count}
					{$i18n.t('KBs')} · {status.file_count}
					{$i18n.t('files')} · {$i18n.t('last sync')}
					{lastSyncLabel} · {statusWord}
					{#if hasSuspended}
						· {$i18n.t('{{count}} suspended', { count: status.suspended_count })}
					{/if}
				{/if}
			</div>
		</div>

		{#if enabled}
			<Badge type="success" content={$i18n.t('Enabled')} />
		{:else}
			<Badge type="muted" content={$i18n.t('Disabled')} />
		{/if}

		<ChevronDown
			className="size-4 shrink-0 text-gray-400 transition-transform duration-150 {expanded
				? 'rotate-180'
				: ''}"
		/>
	</button>

	<!-- Body is always mounted (gated with `hidden`, not `{#if}`) so slotted
	     section components keep their instance bindings and any open modals
	     while the card is collapsed — the orchestrator drives every section's
	     load/persist regardless of which card is expanded. -->
	<div class="px-3.5 pb-4 pt-1 {expanded ? '' : 'hidden'}">
		<slot />
	</div>
</div>
