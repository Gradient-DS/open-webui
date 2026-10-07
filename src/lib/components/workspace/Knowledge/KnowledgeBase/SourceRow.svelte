<script lang="ts">
	import { getContext } from 'svelte';
	import type { Writable } from 'svelte/store';
	import type { i18n as I18n } from 'i18next';
	import dayjs from '$lib/dayjs';
	import relativeTime from 'dayjs/plugin/relativeTime';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import SourceItemIcon from '$lib/components/common/SourceItemIcon.svelte';
	import SourceControls from './SourceControls.svelte';
	import type { SchedulePair } from '../utils/cloudSync';
	import { sourceState } from '../utils/sourceState';

	dayjs.extend(relativeTime);
	const i18n = getContext<Writable<I18n>>('i18n');
	// [Gradient] A cloud source that is not a folder (a single synced file) has
	// no directory row of its own, so it gets one line in the listing. Its path
	// lives in the ⋯ menu, not inline.
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
		<SourceItemIcon kind={view.kind} provider={schedule.source_kind} />
	</div>
	<div class="flex min-w-0 flex-1 items-center gap-2 p-2 text-left">
		<div class="line-clamp-1 text-xs">{schedule.label || $i18n.t(view.label)}</div>
		<span class="shrink-0 text-xs text-gray-400"
			>&middot; {$i18n.t(view.kind === 'file' ? 'File' : 'Folder')}</span
		>
		{#if view.lastSyncedAt}
			<Tooltip content={dayjs(view.lastSyncedAt).format('LLLL')} className="shrink-0">
				<span class="text-xs text-gray-400">
					&middot; {$i18n.t('Updated {{time}}', { time: dayjs(view.lastSyncedAt).fromNow() })}
				</span>
			</Tooltip>
		{:else if view.state !== 'syncing'}
			<span class="shrink-0 text-xs text-gray-400">&middot; {$i18n.t('Not synced yet')}</span>
		{/if}
	</div>
	<SourceControls {knowledgeId} {pair} {writeAccess} {busy} {isAdmin} on:action on:reconnect />
</div>
