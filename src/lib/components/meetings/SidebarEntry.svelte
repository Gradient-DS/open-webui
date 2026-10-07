<script lang="ts">
	// [Gradient] Vergadering link for the sidebar, in its collapsed (icon) and expanded form.
	import { getContext } from 'svelte';
	import type { Writable } from 'svelte/store';
	import type { i18n as i18nType } from 'i18next';

	import { goto } from '$app/navigation';
	import { page } from '$app/stores';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import Mic from '$lib/components/icons/Mic.svelte';

	const i18n: Writable<i18nType> = getContext('i18n');

	let { collapsed = false, onClick }: { collapsed?: boolean; onClick: () => void } = $props();

	const active = $derived($page.url.pathname.startsWith('/meetings'));
</script>

{#if collapsed}
	<div class="">
		<Tooltip content={$i18n.t('Meetings')} placement="right">
			<a
				class=" cursor-pointer flex rounded-xl hover:bg-gray-100 dark:hover:bg-gray-850 transition group {active
					? 'bg-gray-100 dark:bg-gray-850'
					: ''}"
				href="/meetings"
				onclick={(e) => {
					e.stopImmediatePropagation();
					e.preventDefault();
					goto('/meetings');
					onClick();
				}}
				draggable="false"
				aria-label={$i18n.t('Meetings')}
			>
				<div class=" self-center flex items-center justify-center size-9">
					<Mic className="size-4" />
				</div>
			</a>
		</Tooltip>
	</div>
{:else}
	<div class="px-1 flex justify-center text-gray-700 dark:text-gray-300">
		<a
			id="sidebar-meetings-button"
			class="grow flex items-center space-x-2 rounded-xl px-2 py-1.5 hover:bg-gray-100 dark:hover:bg-gray-900 transition {active
				? 'bg-gray-100 dark:bg-gray-900'
				: ''}"
			href="/meetings"
			onclick={onClick}
			draggable="false"
			aria-label={$i18n.t('Meetings')}
		>
			<div class="self-center">
				<Mic className="size-4" strokeWidth="1.5" />
			</div>
			<div class="flex self-center translate-y-[0.5px]">
				<div class=" self-center text-[0.8125rem] leading-5">{$i18n.t('Meetings')}</div>
			</div>
		</a>
	</div>
{/if}
