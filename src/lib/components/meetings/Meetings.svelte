<script lang="ts">
	// [Gradient] Vergadering: the caller's own meetings, laid out like the Notes list. No sharing (D15).
	import { getContext, onMount } from 'svelte';
	import type { Writable } from 'svelte/store';
	import type { i18n as i18nType } from 'i18next';
	import { toast } from 'svelte-sonner';
	import relativeTime from 'dayjs/plugin/relativeTime';

	import { goto } from '$app/navigation';
	import dayjs from '$lib/dayjs';
	import { mobile, showSidebar } from '$lib/stores';
	import { formatNumber, getTimeRange } from '$lib/utils';
	import { deleteMeeting } from '$lib/apis/meetings';
	import ConfirmDialog from '$lib/components/common/ConfirmDialog.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import SplitCreateButton from '$lib/components/common/SplitCreateButton.svelte';
	import DropdownOptions from '$lib/components/common/DropdownOptions.svelte';
	import KbSelectionHeader from '$lib/components/workspace/Knowledge/KnowledgeBase/KbSelectionHeader.svelte';
	import SelectCheckbox from '$lib/components/workspace/Knowledge/KnowledgeBase/SelectCheckbox.svelte';
	import EllipsisHorizontal from '$lib/components/icons/EllipsisHorizontal.svelte';
	import Search from '$lib/components/icons/Search.svelte';
	import XMark from '$lib/components/icons/XMark.svelte';
	import SidebarIcon from '$lib/components/icons/Sidebar.svelte';
	import MeetingMenu from './MeetingMenu.svelte';
	import { partStore } from './parts';
	import { forgetMeeting, meetingList, refreshMeetings } from './list';
	import {
		dayjsLocale,
		groupByRange,
		listMeetings,
		statusLabelKey,
		type MeetingSort,
		type MeetingStatus,
		type MeetingSummary
	} from './meeting';

	dayjs.extend(relativeTime);

	const i18n: Writable<i18nType> = getContext('i18n');

	let failed = $state(false);
	// A revisit shows the kept list at once; the refresh on mount updates it.
	const meetings = $derived($meetingList ?? (failed ? [] : null));
	let query = $state('');
	let status = $state<MeetingStatus | ''>('');
	let sort = $state<MeetingSort>('created_at');
	let direction = $state<'asc' | 'desc'>('desc');
	let deleting = $state<MeetingSummary[]>([]);
	let showDelete = $state(false);
	let openMenuId = $state<string | null>(null);
	let selected = $state<string[]>([]);

	const locale = $derived(dayjsLocale($i18n.languages, dayjs.Ls));
	const created = (meeting: MeetingSummary) => dayjs(meeting.created_at).locale(locale);
	const visible = $derived(
		listMeetings(meetings ?? [], {
			query,
			status: status || null,
			sort,
			ascending: direction === 'asc'
		})
	);
	// Date ranges only follow the order of a date sort; any other sort is one plain list.
	const grouped = $derived(
		sort === 'created_at'
			? groupByRange(visible, (meeting) => getTimeRange(created(meeting).unix()))
			: [['', visible] as [string, MeetingSummary[]]]
	);
	// A selection covers only what is listed: searching or filtering drops what it hides.
	const picked = $derived(visible.filter((meeting) => selected.includes(meeting.thread_id)));
	const toggle = (id: string) =>
		(selected = selected.includes(id) ? selected.filter((item) => item !== id) : [...selected, id]);

	const load = async () => {
		try {
			await refreshMeetings(localStorage.token);
			failed = false;
		} catch (error) {
			console.error(error);
			failed = true;
		}
	};

	const remove = async () => {
		const targets = deleting;
		let removed = 0;
		for (const target of targets) {
			try {
				await deleteMeeting(localStorage.token, target.thread_id);
				await partStore().remove(target.thread_id);
				forgetMeeting(target.thread_id);
				removed += 1;
			} catch (error) {
				toast.error(`${(error as Error)?.message ?? error}`);
			}
		}
		if (removed === 1 && targets.length === 1) toast.success($i18n.t('Meeting deleted'));
		else if (removed > 0) toast.success($i18n.t('{{count}} meetings deleted', { count: removed }));
		selected = selected.filter((id) => !targets.some((target) => target.thread_id === id));
		deleting = [];
	};

	onMount(load);
</script>

<svelte:window
	onkeydown={(e) => {
		if (e.key === 'Escape' && picked.length > 0) selected = [];
	}}
/>

<ConfirmDialog
	bind:show={showDelete}
	title={deleting.length > 1
		? $i18n.t('Delete {{count}} meetings?', { count: deleting.length })
		: $i18n.t('Delete meeting?')}
	message={deleting.length > 1
		? $i18n.t(
				'The transcripts and all outputs of these meetings are deleted. This cannot be undone.'
			)
		: $i18n.t('The transcript and all outputs of this meeting are deleted. This cannot be undone.')}
	confirmLabel={$i18n.t('Delete')}
	onConfirm={remove}
/>

<div class="w-full min-h-full h-full">
	<div class="flex items-center gap-0.5 md:gap-1 mb-1">
		{#if $mobile}
			<div class="{$showSidebar ? 'md:hidden' : ''} flex flex-none items-center">
				<Tooltip content={$showSidebar ? $i18n.t('Close Sidebar') : $i18n.t('Open Sidebar')}>
					<button
						class="cursor-pointer flex rounded-lg hover:bg-gray-100 dark:hover:bg-gray-850 transition"
						aria-label={$showSidebar ? $i18n.t('Close Sidebar') : $i18n.t('Open Sidebar')}
						onclick={() => showSidebar.set(!$showSidebar)}
					>
						<div class="self-center p-1.5">
							<SidebarIcon className="size-4" />
						</div>
					</button>
				</Tooltip>
			</div>
		{/if}

		<div class="flex w-full items-center">
			<div class="flex items-center gap-1 py-1 min-w-0">
				<span class="min-w-fit px-1 text-sm select-none">{$i18n.t('Meetings')}</span>
				<span class="text-sm text-gray-500 dark:text-gray-500">
					{meetings === null ? '' : formatNumber(meetings.length)}
				</span>
			</div>

			<div class="ml-auto flex items-center gap-1">
				<SplitCreateButton
					actions={[{ id: 'meetings-new', label: $i18n.t('Create'), href: '/meetings/new' }]}
				/>
			</div>
		</div>
	</div>

	<div class="space-y-1">
		<div class="flex h-8 flex-1 items-center w-full gap-2">
			<div class="flex min-w-0 flex-1 items-center">
				<div class=" self-center ml-1 mr-3">
					<Search className="size-3.5" />
				</div>
				<input
					class=" w-full text-sm py-1 rounded-r-xl outline-hidden bg-transparent"
					bind:value={query}
					placeholder={$i18n.t('Search Meetings')}
				/>
				{#if query}
					<div class="self-center pl-1.5 translate-y-[0.5px] rounded-l-xl bg-transparent">
						<button
							class="p-0.5 rounded-full hover:bg-gray-100 dark:hover:bg-gray-900 transition"
							aria-label={$i18n.t('Clear search')}
							onclick={() => (query = '')}
						>
							<XMark className="size-3" strokeWidth="2" />
						</button>
					</div>
				{/if}
			</div>

			<div class="flex shrink-0 items-center gap-2 whitespace-nowrap">
				<DropdownOptions
					align="end"
					bind:value={status}
					items={[
						{ value: '', label: $i18n.t('All') },
						{ value: 'ready', label: $i18n.t('Done') },
						{ value: 'recording', label: $i18n.t('Not finished') },
						{ value: 'transcribing', label: $i18n.t('In progress') },
						{ value: 'failed', label: $i18n.t('Failed') }
					]}
				/>
				<DropdownOptions
					align="end"
					bind:value={sort}
					items={[
						{ value: 'created_at', label: $i18n.t('Created') },
						{ value: 'title', label: $i18n.t('Name') }
					]}
				/>
				<DropdownOptions
					align="end"
					bind:value={direction}
					items={[
						{ value: 'asc', label: $i18n.t('Asc') },
						{ value: 'desc', label: $i18n.t('Desc') }
					]}
				/>
			</div>
		</div>

		{#if meetings === null}
			<div class="w-full h-full flex justify-center items-center py-10">
				<Spinner className="size-4" />
			</div>
		{:else if visible.length > 0}
			<KbSelectionHeader
				count={picked.length}
				allSelected={picked.length === visible.length}
				indeterminate={picked.length > 0 && picked.length < visible.length}
				onToggleSelectAll={() =>
					(selected = picked.length > 0 ? [] : visible.map((meeting) => meeting.thread_id))}
				onDelete={() => {
					deleting = picked;
					showDelete = true;
				}}
			/>

			<div class="@container h-full my-1">
				{#each grouped as [timeRange, list], groupIndex (timeRange)}
					{#if timeRange}
						<div class="w-full px-2 pb-1 text-xs text-gray-500 dark:text-gray-500">
							{$i18n.t(timeRange)}
						</div>
					{/if}

					<div class="{grouped.length - 1 !== groupIndex ? 'mb-3' : ''} gap-y-0.5 flex flex-col">
						{#each list as meeting (meeting.thread_id)}
							<!-- svelte-ignore a11y_click_events_have_key_events -->
							<div
								role="button"
								tabindex="0"
								aria-label={$i18n.t('Open meeting')}
								class="group flex min-h-8 w-full cursor-pointer items-center gap-2 rounded-xl px-2 py-[0.375rem] text-left transition hover:bg-gray-50 focus-within:bg-gray-50 dark:hover:bg-gray-900 dark:focus-within:bg-gray-900"
								onclick={() => goto(`/meetings/${encodeURIComponent(meeting.thread_id)}`)}
							>
								<SelectCheckbox
									selected={selected.includes(meeting.thread_id)}
									visible={picked.length > 0}
									onToggle={() => toggle(meeting.thread_id)}
								/>

								<div class="flex min-w-0 flex-1 items-center gap-2">
									<div
										dir="auto"
										class="h-[1.25rem] truncate text-[0.8125rem] leading-5 text-gray-800 group-hover:underline dark:text-gray-200"
									>
										{meeting.title || $i18n.t('Untitled meeting')}
									</div>

									{#if statusLabelKey(meeting.status)}
										<div
											class="shrink-0 text-[0.6875rem] leading-5 text-gray-400 dark:text-gray-600"
										>
											{$i18n.t(statusLabelKey(meeting.status) ?? '')}
										</div>
									{/if}

									<Tooltip content={created(meeting).format('LLLL')}>
										<div
											class="shrink-0 truncate text-[0.6875rem] leading-5 text-gray-400 dark:text-gray-600"
										>
											{created(meeting).fromNow()}
										</div>
									</Tooltip>
								</div>

								<div class="ml-2 flex shrink-0 items-center justify-end gap-2">
									<div
										class="hidden max-w-44 shrink-0 truncate text-right text-[0.6875rem] leading-5 text-gray-500 dark:text-gray-500 md:block"
									>
										{created(meeting).format('LL LT')}
									</div>

									<MeetingMenu
										show={openMenuId === meeting.thread_id}
										onDelete={() => {
											deleting = [meeting];
											showDelete = true;
										}}
										onChange={(open) => (openMenuId = open ? meeting.thread_id : null)}
									>
										<button
											class="flex size-5 shrink-0 items-center justify-center rounded-lg text-gray-400 transition hover:text-gray-700 dark:text-gray-500 dark:hover:text-gray-200"
											type="button"
											aria-label={$i18n.t('Meeting menu')}
											onclick={(e) => {
												// The Dropdown opens itself on this click; toggling here too closed it at once.
												// Only keep the row from opening the meeting.
												e.stopPropagation();
											}}
										>
											<EllipsisHorizontal className="size-3.5" />
										</button>
									</MeetingMenu>
								</div>
							</div>
						{/each}
					</div>
				{/each}
			</div>
		{:else}
			<div class="flex min-h-[calc(100dvh-13rem)] w-full flex-col items-center justify-center">
				<div class="max-w-sm text-center text-gray-900 dark:text-gray-100">
					<div class="mb-1.5 text-sm">
						{failed
							? $i18n.t('Meetings could not be loaded.')
							: query || status
								? $i18n.t('No meetings found')
								: $i18n.t('No meetings yet')}
					</div>
					<div class="text-xs leading-5 text-gray-500">
						{query || status
							? $i18n.t('Try adjusting your search or filter to find what you are looking for.')
							: $i18n.t('Create a meeting to record and transcribe it.')}
					</div>
				</div>
			</div>
		{/if}
	</div>
</div>
