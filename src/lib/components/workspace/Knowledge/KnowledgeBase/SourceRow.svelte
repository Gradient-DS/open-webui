<script lang="ts">
	import { getContext } from 'svelte';
	import type { Writable } from 'svelte/store';
	import type { i18n as I18n } from 'i18next';
	import OneDrive from '$lib/components/icons/OneDrive.svelte';
	import GoogleDrive from '$lib/components/icons/GoogleDrive.svelte';
	import DocumentPage from '$lib/components/icons/DocumentPage.svelte';
	import SourceControls from './SourceControls.svelte';
	import type { SchedulePair } from '../utils/cloudSync';
	import { sourceState } from '../utils/sourceState';

	const i18n = getContext<Writable<I18n>>('i18n');
	// [Gradient] A cloud source that is not a folder (a single synced file) has
	// no directory row of its own, so it gets one line in the listing.
	export let knowledgeId: string;
	export let pair: SchedulePair;
	export let writeAccess = false;
	export let busy = false;
	export let isAdmin = false;
	$: schedule = pair.content ?? pair.acl!;
	$: view = sourceState(pair, schedule.connection);
</script>

<div
	class="group flex w-full items-center rounded-xl bg-transparent px-2 transition hover:bg-gray-100 dark:hover:bg-gray-850"
	role="listitem"
>
	<div class="flex items-center p-1" aria-label={$i18n.t(view.provider)}>
		<svelte:component
			this={schedule.source_kind === 'onedrive'
				? OneDrive
				: schedule.source_kind === 'google_drive'
					? GoogleDrive
					: DocumentPage}
			className="size-3.5"
		/>
	</div>
	<div class="flex flex-1 items-center gap-2 p-2 text-left">
		<div class="line-clamp-1 text-xs">{schedule.label || $i18n.t(view.label)}</div>
		{#if view.path}<span class="line-clamp-1 text-xs text-gray-400">{view.path}</span>{/if}
	</div>
	<SourceControls {knowledgeId} {pair} {writeAccess} {busy} {isAdmin} on:action on:reconnect />
</div>
