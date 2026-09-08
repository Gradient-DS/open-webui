<script lang="ts">
	import { onMount, getContext } from 'svelte';
	import { goto } from '$app/navigation';

<<<<<<< HEAD
	import { WEBUI_NAME, config, mobile, showSidebar, user } from '$lib/stores';
	import { isFeatureEnabled } from '$lib/utils/features';
=======
	import { WEBUI_NAME, config, mobile, showSettings, showSidebar, user } from '$lib/stores';
>>>>>>> upstream/main
	import { page } from '$app/stores';
	import Tooltip from '$lib/components/common/Tooltip.svelte';

	import Sidebar from '$lib/components/icons/Sidebar.svelte';

	const i18n = getContext('i18n');

	let loaded = false;

	onMount(async () => {
		if ($user?.role !== 'admin') {
<<<<<<< HEAD
			await goto('/');
			return;
=======
			await goto('/', { replaceState: true });
		} else if (
			!$config?.features?.enable_plugins &&
			$page.url.pathname.includes('/admin/functions')
		) {
			await goto('/admin', { replaceState: true });
>>>>>>> upstream/main
		}

		// Redirect if trying to access disabled feature
		const pathname = $page.url.pathname;
		if (pathname.includes('/admin/evaluations') && !isFeatureEnabled('admin_evaluations')) {
			await goto('/');
			return;
		}
		if (pathname.includes('/admin/functions') && !isFeatureEnabled('admin_functions')) {
			await goto('/');
			return;
		}
		if (pathname.includes('/admin/settings') && !isFeatureEnabled('admin_settings')) {
			await goto('/');
			return;
		}

		loaded = true;
	});
</script>

<svelte:head>
	<!-- LICENSE covers this Open WebUI browser-title identifier.
	Do not alter, remove, obscure, or replace it except as LICENSE permits:
	https://docs.openwebui.com/license. -->
	<title>
		{$i18n.t('Admin Panel')} / {$WEBUI_NAME}
	</title>
</svelte:head>

{#if loaded}
	<div
		class=" flex flex-col h-screen max-h-[100dvh] flex-1 min-w-0 transition-width duration-200 ease-in-out {$showSidebar
			? 'md:max-w-[calc(100%-var(--sidebar-width))]'
			: 'md:max-w-[calc(100%-42px)]'}  w-full max-w-full"
	>
		<nav class="pb-1 px-2.5 pt-2 backdrop-blur-xl drag-region select-none">
			<div class=" flex items-center gap-0.5 md:gap-1">
				{#if $mobile}
					<div class="{$showSidebar ? 'md:hidden' : ''} self-center flex flex-none items-center">
						<Tooltip
							content={$showSidebar ? $i18n.t('Close Sidebar') : $i18n.t('Open Sidebar')}
							interactive={true}
						>
							<button
								id="sidebar-toggle-button"
								class=" cursor-pointer flex rounded-lg hover:bg-gray-100 dark:hover:bg-gray-850 transition cursor-"
								aria-label={$showSidebar ? $i18n.t('Close Sidebar') : $i18n.t('Open Sidebar')}
								on:click={() => {
									showSidebar.set(!$showSidebar);
								}}
							>
								<div class=" self-center p-1.5">
									<Sidebar className="size-4" />
								</div>
							</button>
						</Tooltip>
					</div>
				{/if}

				<div class="flex w-full items-center">
					<div
<<<<<<< HEAD
						class="flex gap-1 scrollbar-none overflow-x-auto w-fit text-center text-sm font-medium bg-transparent pt-1"
					>
						<a
							draggable="false"
							class="min-w-fit p-1.5 rounded-lg {$page.url.pathname.includes('/admin/users')
								? 'bg-gray-100 dark:bg-gray-800'
								: 'text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-850'} transition select-none"
							href="/admin">{$i18n.t('Users')}</a
						>

						{#if $config?.features.enable_admin_analytics ?? true}
							<a
								draggable="false"
								class="min-w-fit p-1.5 rounded-lg {$page.url.pathname.includes('/admin/analytics')
									? 'bg-gray-100 dark:bg-gray-800'
									: 'text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-850'} transition select-none"
								href="/admin/analytics">{$i18n.t('Analytics')}</a
							>
						{/if}

						{#if isFeatureEnabled('admin_evaluations')}
							<a
								draggable="false"
								class="min-w-fit p-1.5 rounded-lg {$page.url.pathname.includes('/admin/evaluations')
									? 'bg-gray-100 dark:bg-gray-800'
									: 'text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-850'} transition select-none"
								href="/admin/evaluations">{$i18n.t('Evaluations')}</a
							>
						{/if}

						{#if isFeatureEnabled('admin_functions')}
							<a
								draggable="false"
								class="min-w-fit p-1.5 rounded-lg {$page.url.pathname.includes('/admin/functions')
									? 'bg-gray-100 dark:bg-gray-800'
									: 'text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-850'} transition select-none"
=======
						class="flex min-w-0 mr-1.5 items-center gap-0.5 md:gap-1 scrollbar-none overflow-x-auto w-fit text-center text-sm font-normal rounded-full bg-transparent py-1 touch-auto pointer-events-auto"
					>
						<a
							draggable="false"
							class="min-w-fit px-1 text-sm {$page.url.pathname.includes('/admin/users')
								? ''
								: 'text-gray-300 dark:text-gray-600 hover:text-gray-700 dark:hover:text-white'} transition select-none"
							href="/admin">{$i18n.t('Users')}</a
						>

						<a
							draggable="false"
							class="min-w-fit px-1 text-sm {$page.url.pathname.includes('/admin/evaluations')
								? ''
								: 'text-gray-300 dark:text-gray-600 hover:text-gray-700 dark:hover:text-white'} transition select-none"
							href="/admin/evaluations">{$i18n.t('Evaluations')}</a
						>

						{#if $config?.features?.enable_plugins}
							<a
								draggable="false"
								class="min-w-fit px-1 text-sm {$page.url.pathname.includes('/admin/functions')
									? ''
									: 'text-gray-300 dark:text-gray-600 hover:text-gray-700 dark:hover:text-white'} transition select-none"
>>>>>>> upstream/main
								href="/admin/functions">{$i18n.t('Functions')}</a
							>
						{/if}

<<<<<<< HEAD
						{#if isFeatureEnabled('admin_settings')}
							<a
								draggable="false"
								class="min-w-fit p-1.5 rounded-lg {$page.url.pathname.includes('/admin/settings')
									? 'bg-gray-100 dark:bg-gray-800'
									: 'text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-850'} transition select-none"
								href="/admin/settings">{$i18n.t('Settings')}</a
							>
						{/if}
=======
						<a
							draggable="false"
							class="min-w-fit px-1 text-sm {$page.url.pathname.includes('/admin/settings')
								? ''
								: 'text-gray-300 dark:text-gray-600 hover:text-gray-700 dark:hover:text-white'} transition select-none"
							href="/admin/settings"
							on:click={(event) => {
								event.preventDefault();
								showSettings.set('admin:general');
							}}>{$i18n.t('Settings')}</a
						>
>>>>>>> upstream/main
					</div>
				</div>
			</div>
		</nav>

		<div class="  pb-1 flex-1 min-w-0 max-h-full overflow-y-auto overflow-x-hidden">
			<slot />
		</div>
	</div>
{/if}
