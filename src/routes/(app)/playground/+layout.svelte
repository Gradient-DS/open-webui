<script lang="ts">
	import { onMount, getContext } from 'svelte';
	import { goto } from '$app/navigation';
	import { WEBUI_NAME, showSidebar, functions, mobile, user } from '$lib/stores';
	import { page } from '$app/stores';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import Sidebar from '$lib/components/icons/Sidebar.svelte';
	import { isFeatureEnabled } from '$lib/utils/features';

	const i18n = getContext('i18n');

	onMount(async () => {
		// Check feature flag - applies to everyone including admins
		if (!isFeatureEnabled('playground')) {
			goto('/');
			return;
		}

		// Check admin role (existing behavior)
		if ($user?.role !== 'admin') {
			goto('/');
			return;
		}
	});
</script>

<svelte:head>
	<title>
		{$i18n.t('Playground')} • {$WEBUI_NAME}
	</title>
</svelte:head>

<div
	class=" flex flex-col w-full h-screen max-h-[100dvh] transition-width duration-200 ease-in-out {$showSidebar
		? 'md:max-w-[calc(100%-var(--sidebar-width))]'
		: ''} max-w-full"
>
	<nav class="   px-2.5 pt-1.5 backdrop-blur-xl w-full drag-region select-none">
		<div class=" flex items-center">
			{#if $mobile}
				<div class="{$showSidebar ? 'md:hidden' : ''} flex flex-none items-center self-end">
					<Tooltip
						content={$showSidebar ? $i18n.t('Close Sidebar') : $i18n.t('Open Sidebar')}
						interactive={true}
					>
						<button
							id="sidebar-toggle-button"
							class=" cursor-pointer flex rounded-lg hover:bg-gray-100 dark:hover:bg-gray-850 transition cursor-"
							on:click={() => {
								showSidebar.set(!$showSidebar);
							}}
						>
							<div class=" self-center p-1.5">
								<Sidebar />
							</div>
						</button>
					</Tooltip>
				</div>
			{/if}

			<div class=" flex w-full">
				<div
					class="flex gap-1 scrollbar-none overflow-x-auto w-fit text-center text-sm font-medium bg-transparent pt-1"
				>
					<a
						draggable="false"
						class="min-w-fit p-1.5 rounded-lg {['/playground', '/playground/'].includes(
							$page.url.pathname
						)
							? 'bg-gray-100 dark:bg-gray-800'
							: 'text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-850'} transition select-none"
						href="/playground">{$i18n.t('Chat')}</a
					>

					<!-- <a
						class="min-w-fit p-1.5 rounded-lg {$page.url.pathname.includes('/playground/notes')
							? 'bg-gray-100 dark:bg-gray-800'
							: 'text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-850'} transition"
						href="/playground/notes">{$i18n.t('Notes')}</a
					> -->

					<a
						draggable="false"
						class="min-w-fit p-1.5 rounded-lg {$page.url.pathname.includes(
							'/playground/completions'
						)
							? 'bg-gray-100 dark:bg-gray-800'
							: 'text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-850'} transition select-none"
						href="/playground/completions">{$i18n.t('Completions')}</a
					>

					<a
						draggable="false"
						class="min-w-fit p-1.5 rounded-lg {$page.url.pathname.includes('/playground/images')
							? 'bg-gray-100 dark:bg-gray-800'
							: 'text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-850'} transition select-none"
						href="/playground/images">{$i18n.t('Images')}</a
					>
				</div>
			</div>
		</div>
	</nav>

	<div class=" flex-1 max-h-full overflow-y-auto">
		<slot />
	</div>
</div>
