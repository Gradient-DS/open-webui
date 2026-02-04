<script lang="ts">
	import { getContext, createEventDispatcher } from 'svelte';
	import Modal from '$lib/components/common/Modal.svelte';
	import XMark from '$lib/components/icons/XMark.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import { removeGroupFromKnowledge } from '$lib/apis/knowledge';
	import { toast } from 'svelte-sonner';

	const i18n = getContext('i18n');
	const dispatch = createEventDispatcher();

	export let show = false;
	export let kbConflicts: {
		knowledge_id: string;
		knowledge_name: string;
		onedrive_sources: { name: string; url: string | null }[];
		users_without_access: { user_id: string; user_name: string; user_email: string }[];
	}[] = [];
	export let groupId = '';
	export let userName = '';

	let removingKbId: string | null = null;

	async function handleRemoveGroup(knowledgeId: string) {
		removingKbId = knowledgeId;
		try {
			await removeGroupFromKnowledge(localStorage.token, knowledgeId, groupId);
			kbConflicts = kbConflicts.filter((c) => c.knowledge_id !== knowledgeId);

			if (kbConflicts.length === 0) {
				show = false;
				dispatch('resolved');
			}
		} catch (err) {
			toast.error(`${err}`);
		} finally {
			removingKbId = null;
		}
	}

	function handleCancel() {
		dispatch('cancel');
		show = false;
	}
</script>

<Modal size="md" bind:show>
	<div>
		<div class="flex justify-between dark:text-gray-100 px-5 pt-3 pb-1">
			<div class="flex items-center gap-3">
				<div class="p-2 bg-yellow-100 dark:bg-yellow-900/30 rounded-full">
					<svg
						xmlns="http://www.w3.org/2000/svg"
						fill="none"
						viewBox="0 0 24 24"
						stroke-width="1.5"
						stroke="currentColor"
						class="w-5 h-5 text-yellow-600 dark:text-yellow-400"
					>
						<path
							stroke-linecap="round"
							stroke-linejoin="round"
							d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126zM12 15.75h.007v.008H12v-.008z"
						/>
					</svg>
				</div>
				<div>
					<div class="text-lg font-medium self-center font-primary">
						{$i18n.t('Cannot Add User')}
					</div>
				</div>
			</div>
			<button class="self-center" on:click={handleCancel}>
				<XMark className={'size-5'} />
			</button>
		</div>

		<div class="w-full px-5 pb-4 dark:text-white">
			<p class="text-sm text-gray-600 dark:text-gray-400 mb-4">
				{$i18n.t(
					'"{{userName}}" cannot be added to this group because the group has access to knowledge bases with source-restricted files that this user doesn\'t have access to.',
					{ userName }
				)}
			</p>

			<div class="space-y-3 mb-4">
				{#each kbConflicts as conflict (conflict.knowledge_id)}
					<div class="border border-gray-200 dark:border-gray-700 rounded-lg p-3">
						<div class="font-medium text-sm mb-1">
							{$i18n.t('KB: "{{name}}"', { name: conflict.knowledge_name })}
						</div>

						{#if conflict.onedrive_sources.length > 0}
							<div class="text-xs text-gray-500 dark:text-gray-400 mb-2 flex flex-wrap items-center gap-1">
								<span>{$i18n.t('OneDrive folder:')}</span>
								{#each conflict.onedrive_sources as source, idx}
									{#if source.url}
										<a
											href={source.url}
											target="_blank"
											rel="noopener noreferrer"
											class="text-blue-600 dark:text-blue-400 hover:underline"
										>{source.name}</a>
									{:else}
										<span>{source.name}</span>
									{/if}
									{#if idx < conflict.onedrive_sources.length - 1}<span>,</span>{/if}
								{/each}
							</div>
						{/if}

						<div class="text-xs text-gray-600 dark:text-gray-400 mb-2">
							{$i18n.t('Users without access:')}
						</div>
						<ul class="text-xs text-gray-700 dark:text-gray-300 mb-3 space-y-0.5">
							{#each conflict.users_without_access as user}
								<li class="flex items-center gap-1">
									<span class="text-gray-400">&bull;</span>
									<span>{user.user_email || user.user_name}</span>
								</li>
							{/each}
						</ul>

						<div class="flex items-center gap-3">
							<button
								class="text-xs font-medium px-3 py-1.5 rounded-lg bg-red-50 dark:bg-red-900/20 text-red-700 dark:text-red-300 hover:bg-red-100 dark:hover:bg-red-900/40 disabled:opacity-50"
								disabled={removingKbId === conflict.knowledge_id}
								on:click={() => handleRemoveGroup(conflict.knowledge_id)}
							>
								{#if removingKbId === conflict.knowledge_id}
									<Spinner className="size-3 inline mr-1" />
								{/if}
								{$i18n.t('Remove group from KB')}
							</button>
							{#if conflict.onedrive_sources.some((s) => s.url)}
								{@const grantUrl = conflict.onedrive_sources.find((s) => s.url)?.url}
								<a
									href={grantUrl}
									target="_blank"
									rel="noopener noreferrer"
									class="text-xs text-blue-600 dark:text-blue-400 hover:underline"
								>
									{$i18n.t('Grant access')} ↗
								</a>
							{:else}
								<span class="text-xs text-gray-500 dark:text-gray-400">
									{$i18n.t('or grant access in OneDrive first')}
								</span>
							{/if}
						</div>
					</div>
				{/each}
			</div>

			<div class="text-xs text-gray-500 dark:text-gray-400 mb-4">
				{$i18n.t(
					'Options: Remove the group from the listed knowledge bases, or grant OneDrive access to the user first.'
				)}
			</div>

			<div class="flex justify-end">
				<button
					class="px-4 py-2 text-sm font-medium text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700 rounded-lg"
					on:click={handleCancel}
				>
					{$i18n.t('Go Back')}
				</button>
			</div>
		</div>
	</div>
</Modal>
