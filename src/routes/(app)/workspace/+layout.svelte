<script lang="ts">
	import { onMount, getContext } from 'svelte';
	import type { Writable } from 'svelte/store';
	import type { i18n as i18nType } from 'i18next';
	import {
		WEBUI_NAME,
		config,
		showSidebar,
		user,
		mobile,
		workspaceActions,
		workspaceCounts
	} from '$lib/stores';
	import { page } from '$app/stores';
	// [Gradient] Tenant feature gates also apply to administrators.
	import { isFeatureEnabled } from '$lib/utils/features';
	import { goto } from '$app/navigation';
	import { getModelItems } from '$lib/apis/models';
	import { searchKnowledgeBases } from '$lib/apis/knowledge';
	import { getPromptItems } from '$lib/apis/prompts';
	import { getSkillItems } from '$lib/apis/skills';
	import { getToolList } from '$lib/apis/tools';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import Sidebar from '$lib/components/icons/Sidebar.svelte';
	import SplitCreateButton from '$lib/components/common/SplitCreateButton.svelte';

	const i18n = getContext<Writable<i18nType>>('i18n');

	let loaded = false;
	let lastPath = '';
	let visibleActions = [];

	$: if ($page.url.pathname !== lastPath) {
		lastPath = $page.url.pathname;
		workspaceActions.set([]);
	}

	$: if (loaded && $page.url.pathname.startsWith('/workspace')) {
		loadWorkspaceCounts();
	}

	$: visibleActions = $workspaceActions.filter((action) => action.visible ?? true);

	const getCount = (res: any) => res?.total ?? (Array.isArray(res) ? res.length : null);

	const loadWorkspaceCounts = async () => {
		const canViewModels =
			isFeatureEnabled('models') &&
			($user?.role === 'admin' || $user?.permissions?.workspace?.models);
		const canViewKnowledge =
			isFeatureEnabled('knowledge') &&
			($user?.role === 'admin' || $user?.permissions?.workspace?.knowledge);
		const canViewPrompts =
			isFeatureEnabled('prompts') &&
			($user?.role === 'admin' || $user?.permissions?.workspace?.prompts);
		const canViewSkills =
			isFeatureEnabled('skills') &&
			($user?.role === 'admin' || $user?.permissions?.workspace?.skills);
		const canViewTools =
			isFeatureEnabled('tools') &&
			$config?.features?.enable_plugins &&
			($user?.role === 'admin' || $user?.permissions?.workspace?.tools);

		const [modelRes, knowledgeRes, promptRes, skillRes, toolRes] = await Promise.all([
			canViewModels
				? getModelItems(localStorage.token, null, null, null, null, null, 1).catch(() => null)
				: null,
			canViewKnowledge
				? searchKnowledgeBases(localStorage.token, null, null, 1, null, null, null, null).catch(
						() => null
					)
				: null,
			canViewPrompts
				? getPromptItems(localStorage.token, null, null, null, null, null, 1).catch(() => null)
				: null,
			canViewSkills ? getSkillItems(localStorage.token, null, null, 1).catch(() => null) : null,
			canViewTools ? getToolList(localStorage.token).catch(() => null) : null
		]);

		workspaceCounts.set({
			models: getCount(modelRes),
			knowledge: getCount(knowledgeRes),
			prompts: getCount(promptRes),
			skills: getCount(skillRes),
			tools: getCount(toolRes)
		});
	};

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

		if ($page.url.pathname.includes('/skills') && !isFeatureEnabled('skills')) {
			goto('/');
			return;
		}

		// Permission checks for non-admin users
		if ($user?.role !== 'admin') {
			if ($page.url.pathname.includes('/models') && !$user?.permissions?.workspace?.models) {
				goto('/', { replaceState: true });
			} else if (
				$page.url.pathname.includes('/knowledge') &&
				!$user?.permissions?.workspace?.knowledge
			) {
				goto('/', { replaceState: true });
			} else if (
				$page.url.pathname.includes('/prompts') &&
				!$user?.permissions?.workspace?.prompts
			) {
				goto('/', { replaceState: true });
			} else if (
				$page.url.pathname.includes('/tools') &&
				(!$config?.features?.enable_plugins || !$user?.permissions?.workspace?.tools)
			) {
				goto('/', { replaceState: true });
			} else if ($page.url.pathname.includes('/skills') && !$user?.permissions?.workspace?.skills) {
				goto('/', { replaceState: true });
			}
		}

		loaded = true;
	});
</script>

<svelte:head>
	<!-- LICENSE covers this Open WebUI browser-title identifier.
	Do not alter, remove, obscure, or replace it except as LICENSE permits:
	https://docs.openwebui.com/license. -->
	<title>
		{$i18n.t('Agents & prompts')} • {$WEBUI_NAME}
	</title>
</svelte:head>

{#if loaded}
	<div
		class="flex flex-col flex-1 min-w-0 w-full h-screen max-h-[100dvh] transition-width duration-200 ease-in-out {$showSidebar
			? 'md:max-w-[calc(100%-var(--sidebar-width))]'
			: 'md:max-w-[calc(100%-42px)]'} max-w-full"
	>
		<nav class="pb-1 px-2.5 pt-2 backdrop-blur-xl drag-region select-none">
			<div class="flex items-center gap-0.5 md:gap-1">
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

				<!-- [Gradient] No section tabs here: the sidebar is the only navigation for
				     Knowledge, Agents, Prompts, Skills and Tools. Upstream's always-on
				     workspace nav duplicated it row for row. The create action stays. -->
				<div class="flex w-full items-center">
					<div class="ml-auto flex shrink-0 items-center gap-1">
						<SplitCreateButton actions={visibleActions} />
					</div>
				</div>

				<!-- <div class="flex items-center text-xl font-normal">{$i18n.t('Workspace')}</div> -->
			</div>
		</nav>

		<div
			class="pb-1 px-3 md:px-[18px] flex-1 min-h-0 min-w-0 max-h-full {$page.url.pathname.includes(
				'/workspace/knowledge/'
			)
				? 'pt-4 overflow-hidden'
				: 'overflow-y-auto overflow-x-hidden'}"
			id="workspace-container"
		>
			<slot />
		</div>
	</div>
{/if}
