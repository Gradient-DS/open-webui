<script lang="ts">
	// [Gradient] "Attach meetings": the user's meetings, newest first, as the Notes submenu lists notes.
	// Only a reference is attached; the server reads the meeting from soev-api at send time.
	import dayjs from 'dayjs';
	import relativeTime from 'dayjs/plugin/relativeTime';
	import { getContext, onMount } from 'svelte';
	import type { Writable } from 'svelte/store';
	import type { i18n as i18nType } from 'i18next';

	import { getMeetings } from '$lib/apis/meetings';
	import { matchesQuery, type MeetingSummary } from '$lib/components/meetings/meeting';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import Mic from '$lib/components/icons/Mic.svelte';
	import SearchInput from './SearchInput.svelte';

	dayjs.extend(relativeTime);

	const i18n: Writable<i18nType> = getContext('i18n');

	let { onSelect }: { onSelect: (item: Record<string, unknown>) => void } = $props();

	let meetings = $state<MeetingSummary[] | null>(null);
	let query = $state('');
	let selectedIdx = $state(0);

	const items = $derived(
		[...(meetings ?? [])]
			.sort((a, b) => (b.created_at ?? '').localeCompare(a.created_at ?? ''))
			.filter((meeting) => matchesQuery(meeting.title, query))
			.map((meeting) => ({
				type: 'meeting',
				id: meeting.thread_id,
				name: meeting.title || $i18n.t('Untitled meeting'),
				description: dayjs(meeting.created_at).fromNow()
			}))
	);

	onMount(async () => {
		meetings = (await getMeetings(localStorage.token).catch(() => null))?.data ?? [];
	});
</script>

{#if meetings !== null}
	<div class="flex min-h-0 flex-1 flex-col gap-0.5 overflow-hidden">
		<SearchInput bind:value={query} placeholder={$i18n.t('Search Meetings')} />

		<div class="min-h-0 flex-1 overflow-y-auto overflow-x-hidden scrollbar-thin">
			{#if items.length === 0}
				<div class="text-center text-xs text-gray-500 py-3">{$i18n.t('No meetings found')}</div>
			{:else}
				<div class="flex flex-col gap-0.5">
					{#each items as item, idx (item.id)}
						<button
							class=" h-[1.6875rem] px-2 rounded-xl w-full text-left flex justify-between items-center text-[0.8125rem] font-normal {idx ===
							selectedIdx
								? ' bg-gray-50/40 dark:bg-gray-800/40 dark:text-gray-100 selected-command-option-button'
								: ''}"
							type="button"
							onclick={() => onSelect(item)}
							onmousemove={() => (selectedIdx = idx)}
							data-selected={idx === selectedIdx}
						>
							<div class="text-black dark:text-gray-100 flex items-center gap-1.5">
								<Tooltip content={$i18n.t('Meeting')} placement="top">
									<Mic className="size-3.5" />
								</Tooltip>
								<Tooltip content={item.description} placement="top-start">
									<div class="line-clamp-1 flex-1">{item.name}</div>
								</Tooltip>
							</div>
						</button>
					{/each}
				</div>
			{/if}
		</div>
	</div>
{:else}
	<div class="py-4.5">
		<Spinner />
	</div>
{/if}
