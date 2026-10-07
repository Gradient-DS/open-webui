<script lang="ts">
	// [Gradient] Vergadering: the caller's own meetings, newest first. No sharing (D15).
	import { getContext, onMount } from 'svelte';
	import type { Writable } from 'svelte/store';
	import type { i18n as i18nType } from 'i18next';
	import { toast } from 'svelte-sonner';

	import { goto } from '$app/navigation';
	import dayjs from '$lib/dayjs';
	import { deleteMeeting, getMeetings } from '$lib/apis/meetings';
	import ConfirmDialog from '$lib/components/common/ConfirmDialog.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import GarbageBin from '$lib/components/icons/GarbageBin.svelte';
	import Plus from '$lib/components/icons/Plus.svelte';
	import type { MeetingSummary } from './meeting';

	const i18n: Writable<i18nType> = getContext('i18n');

	let meetings = $state<MeetingSummary[] | null>(null);
	let failed = $state(false);
	let deleting = $state<MeetingSummary | null>(null);
	let showDelete = $state(false);

	const sorted = $derived(
		[...(meetings ?? [])].sort((a, b) => (b.created_at ?? '').localeCompare(a.created_at ?? ''))
	);

	const load = async () => {
		try {
			meetings = (await getMeetings(localStorage.token)).data ?? [];
			failed = false;
		} catch (error) {
			console.error(error);
			failed = true;
			meetings = [];
		}
	};

	const remove = async () => {
		const target = deleting;
		if (!target) return;
		try {
			await deleteMeeting(localStorage.token, target.thread_id);
			meetings = (meetings ?? []).filter((meeting) => meeting.thread_id !== target.thread_id);
			toast.success($i18n.t('Meeting deleted'));
		} catch (error) {
			toast.error(`${(error as Error)?.message ?? error}`);
		} finally {
			deleting = null;
		}
	};

	onMount(load);
</script>

<ConfirmDialog
	bind:show={showDelete}
	title={$i18n.t('Delete meeting?')}
	message={$i18n.t(
		'The transcript and all outputs of this meeting are deleted. This cannot be undone.'
	)}
	confirmLabel={$i18n.t('Delete')}
	onConfirm={remove}
/>

<div class="max-w-4xl mx-auto w-full px-4 pt-3 pb-10">
	<div class="flex items-center justify-between gap-2 mb-4">
		<h1 class="text-lg font-medium dark:text-gray-100">{$i18n.t('Meetings')}</h1>
		<button
			type="button"
			class="flex items-center gap-1.5 px-3.5 py-1.5 text-sm rounded-full bg-black text-white dark:bg-white dark:text-black transition"
			onclick={() => goto('/meetings/new')}
		>
			<Plus className="size-3.5" strokeWidth="2" />
			{$i18n.t('New meeting')}
		</button>
	</div>

	{#if meetings === null}
		<div class="flex justify-center py-16"><Spinner className="size-5" /></div>
	{:else if failed}
		<div class="py-16 text-center text-sm text-gray-500">
			{$i18n.t('Meetings could not be loaded.')}
		</div>
	{:else if sorted.length === 0}
		<div class="py-16 text-center text-sm text-gray-500">
			{$i18n.t('No meetings yet. Start one to record and transcribe it.')}
		</div>
	{:else}
		<ul class="divide-y divide-gray-100 dark:divide-gray-850">
			{#each sorted as meeting (meeting.thread_id)}
				<li class="flex items-center gap-2 group">
					<a
						class="flex-1 min-w-0 py-2.5 px-2 rounded-xl hover:bg-gray-50 dark:hover:bg-gray-900 transition"
						href={`/meetings/${encodeURIComponent(meeting.thread_id)}`}
					>
						<div class="text-sm line-clamp-1 dark:text-gray-100">
							{meeting.title || $i18n.t('Untitled meeting')}
						</div>
						<div class="text-xs text-gray-500">{dayjs(meeting.created_at).format('LLL')}</div>
					</a>
					<Tooltip content={$i18n.t('Delete meeting')}>
						<button
							type="button"
							class="p-1.5 rounded-lg text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-850 transition"
							aria-label={$i18n.t('Delete meeting')}
							onclick={() => {
								deleting = meeting;
								showDelete = true;
							}}
						>
							<GarbageBin className="size-4" />
						</button>
					</Tooltip>
				</li>
			{/each}
		</ul>
	{/if}
</div>
