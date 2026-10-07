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
	import { deleteMeeting, getMeetings } from '$lib/apis/meetings';
	import ConfirmDialog from '$lib/components/common/ConfirmDialog.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import SplitCreateButton from '$lib/components/common/SplitCreateButton.svelte';
	import EllipsisHorizontal from '$lib/components/icons/EllipsisHorizontal.svelte';
	import Search from '$lib/components/icons/Search.svelte';
	import XMark from '$lib/components/icons/XMark.svelte';
	import SidebarIcon from '$lib/components/icons/Sidebar.svelte';
	import MeetingMenu from './MeetingMenu.svelte';
	import { dayjsLocale, groupByRange, matchesQuery, type MeetingSummary } from './meeting';

	dayjs.extend(relativeTime);

	const i18n: Writable<i18nType> = getContext('i18n');

	let meetings = $state<MeetingSummary[] | null>(null);
	let failed = $state(false);
	let query = $state('');
	let deleting = $state<MeetingSummary | null>(null);
	let showDelete = $state(false);
	let openMenuId = $state<string | null>(null);

	const locale = $derived(dayjsLocale($i18n.languages, dayjs.Ls));
	const created = (meeting: MeetingSummary) => dayjs(meeting.created_at).locale(locale);
	const sorted = $derived(
		[...(meetings ?? [])].sort((a, b) => (b.created_at ?? '').localeCompare(a.created_at ?? ''))
	);
	const visible = $derived(sorted.filter((meeting) => matchesQuery(meeting.title, query)));
	const grouped = $derived(
		groupByRange(visible, (meeting) => getTimeRange(created(meeting).unix()))
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
		</div>

		{#if meetings === null}
			<div class="w-full h-full flex justify-center items-center py-10">
				<Spinner className="size-4" />
			</div>
		{:else if visible.length > 0}
			<div class="@container h-full my-1">
				{#each grouped as [timeRange, list], groupIndex (timeRange)}
					<div class="w-full px-2 pb-1 text-xs text-gray-500 dark:text-gray-500">
						{$i18n.t(timeRange)}
					</div>

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
								<div class="flex min-w-0 flex-1 items-center gap-2">
									<div
										dir="auto"
										class="h-[1.25rem] truncate text-[0.8125rem] leading-5 text-gray-800 group-hover:underline dark:text-gray-200"
									>
										{meeting.title || $i18n.t('Untitled meeting')}
									</div>

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
											deleting = meeting;
											showDelete = true;
										}}
										onChange={(open) => (openMenuId = open ? meeting.thread_id : null)}
									>
										<button
											class="flex size-5 shrink-0 items-center justify-center rounded-lg text-gray-400 transition hover:text-gray-700 dark:text-gray-500 dark:hover:text-gray-200"
											type="button"
											aria-label={$i18n.t('Meeting menu')}
											onclick={(e) => {
												e.preventDefault();
												e.stopPropagation();
												openMenuId = openMenuId === meeting.thread_id ? null : meeting.thread_id;
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
							: query
								? $i18n.t('No meetings found')
								: $i18n.t('No meetings yet')}
					</div>
					<div class="text-xs leading-5 text-gray-500">
						{query
							? $i18n.t('Try adjusting your search or filter to find what you are looking for.')
							: $i18n.t('Create a meeting to record and transcribe it.')}
					</div>
				</div>
			</div>
		{/if}
	</div>
</div>
