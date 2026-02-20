<script lang="ts">
	import { onMount, getContext } from 'svelte';
	import {
		WEBUI_NAME,
		showSidebar,
		functions,
		user,
		mobile,
		models,
		prompts,
		knowledge,
		tools
	} from '$lib/stores';
	import { page } from '$app/stores';
	import { goto } from '$app/navigation';
	import { isFeatureEnabled } from '$lib/utils/features';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import Sidebar from '$lib/components/icons/Sidebar.svelte';

	const i18n = getContext('i18n');

	let loaded = false;

	onMount(async () => {
		// Feature flag checks apply to ALL users including admins
		if ($page.url.pathname.includes('/models') && !isFeatureEnabled('models')) {
			goto('/');
			return;
		}
		if ($page.url.pathname.includes('/knowledge') && !isFeatureEnabled('knowledge')) {
			goto('/');
			return;
		}
		if ($page.url.pathname.includes('/prompts') && !isFeatureEnabled('prompts')) {
			goto('/');
			return;
		}
		if ($page.url.pathname.includes('/tools') && !isFeatureEnabled('tools')) {
			goto('/');
			return;
		}

		// Permission checks for non-admin users
		if ($user?.role !== 'admin') {
			if ($page.url.pathname.includes('/models') && !$user?.permissions?.workspace?.models) {
				goto('/');
			} else if (
				$page.url.pathname.includes('/knowledge') &&
				!$user?.permissions?.workspace?.knowledge
			) {
				goto('/');
			} else if (
				$page.url.pathname.includes('/prompts') &&
				!$user?.permissions?.workspace?.prompts
			) {
				goto('/');
			} else if ($page.url.pathname.includes('/tools') && !$user?.permissions?.workspace?.tools) {
				goto('/');
			}
		}

		loaded = true;
	});
</script>

<svelte:head>
	<title>
		{$i18n.t('Agents & prompts')} • {$WEBUI_NAME}
	</title>
</svelte:head>

{#if loaded}
	<div
		class=" relative flex flex-col w-full h-screen max-h-[100dvh] transition-width duration-200 ease-in-out {$showSidebar
			? 'md:max-w-[calc(100%-var(--sidebar-width))]'
			: ''} max-w-full"
	>
		{#if !$page.url.pathname.includes('/workspace/knowledge')}
		<nav class="   px-2.5 pt-1.5 backdrop-blur-xl drag-region">
			<div class=" flex items-center gap-1">
				{#if $mobile}
					<div class="{$showSidebar ? 'md:hidden' : ''} self-center flex flex-none items-center">
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

				<div class="">
					<div
						class="flex gap-1 scrollbar-none overflow-x-auto w-fit text-center text-sm font-medium rounded-full bg-transparent py-1 touch-auto pointer-events-auto"
					>
						{#if isFeatureEnabled('models') && ($user?.role === 'admin' || $user?.permissions?.workspace?.models)}
							<a
								class="min-w-fit p-1.5 {$page.url.pathname.includes('/workspace/models')
									? ''
									: 'text-gray-300 dark:text-gray-600 hover:text-gray-700 dark:hover:text-white'} transition"
								href="/workspace/models">{$i18n.t('Agents')}</a
							>
						{/if}


						{#if isFeatureEnabled('prompts') && ($user?.role === 'admin' || $user?.permissions?.workspace?.prompts)}
							<a
								class="min-w-fit p-1.5 {$page.url.pathname.includes('/workspace/prompts')
									? ''
									: 'text-gray-300 dark:text-gray-600 hover:text-gray-700 dark:hover:text-white'} transition"
								href="/workspace/prompts">{$i18n.t('Prompts')}</a
							>
						{/if}

						{#if isFeatureEnabled('tools') && ($user?.role === 'admin' || $user?.permissions?.workspace?.tools)}
							<a
								class="min-w-fit p-1.5 {$page.url.pathname.includes('/workspace/tools')
									? ''
									: 'text-gray-300 dark:text-gray-600 hover:text-gray-700 dark:hover:text-white'} transition"
								href="/workspace/tools"
							>
								{$i18n.t('Tools')}
							</a>
						{/if}
					</div>
				</div>

				<!-- <div class="flex items-center text-xl font-medium">{$i18n.t('Workspace')}</div> -->
			</div>
		</nav>
		{/if}

		<div
			class="  pb-1 px-3 md:px-[18px] flex-1 max-h-full overflow-y-auto {$page.url.pathname.includes('/workspace/knowledge') ? 'pt-4' : ''}"
			id="workspace-container"
		>
			<slot />
		</div>
	</div>
{/if}
