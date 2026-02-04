<script lang="ts">
	import { getContext, createEventDispatcher } from 'svelte';
	import Modal from '$lib/components/common/Modal.svelte';
	import XMark from '$lib/components/icons/XMark.svelte';
	import type { FileAdditionConflict, SharingRecommendation, GroupConflict } from '$lib/apis/knowledge/permissions';

	const i18n = getContext('i18n');
	const dispatch = createEventDispatcher();

	export let show = false;
	export let conflict: FileAdditionConflict | null = null;
	export let strictMode = true;

	$: usersWithoutAccessCount = conflict?.users_without_access?.length ?? 0;
	$: hasGroupConflicts = (conflict?.group_conflicts?.length ?? 0) > 0;

	function handleMakePrivate() {
		dispatch('makePrivate');
	}

	function handleContinue() {
		dispatch('continue');
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
				<div class="text-lg font-medium self-center font-primary">
					{$i18n.t('Access Conflict')}
				</div>
			</div>
			<button class="self-center" on:click={handleCancel}>
				<XMark className={'size-5'} />
			</button>
		</div>

		<div class="w-full px-5 pb-4 dark:text-white">
			{#if conflict}
				{#if conflict.kb_is_public}
					<!-- Public KB conflict -->
					<div class="mb-4 p-3 bg-yellow-50 dark:bg-yellow-900/20 rounded-lg">
						<p class="text-sm text-yellow-800 dark:text-yellow-200">
							{$i18n.t(
								'This knowledge base is public, but the files you are adding have restricted access in {{source}}.',
								{ source: conflict.source_type || 'the source' }
							)}
						</p>
						<p class="text-sm text-yellow-700 dark:text-yellow-300 mt-2">
							{$i18n.t(
								'To add these files, the knowledge base must be made private first. You can then share it with specific users who have source access.'
							)}
						</p>
					</div>
				{:else}
					<!-- Shared KB with users lacking source access -->
					<div class="mb-4 p-3 bg-yellow-50 dark:bg-yellow-900/20 rounded-lg">
						<div class="flex items-center gap-2 text-yellow-800 dark:text-yellow-200 mb-2">
							<svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
								<path
									stroke-linecap="round"
									stroke-linejoin="round"
									stroke-width="2"
									d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"
								/>
							</svg>
							<span class="font-medium">
								{$i18n.t('{{count}} users lack source access', { count: usersWithoutAccessCount })}
							</span>
						</div>

						<p class="text-sm text-yellow-700 dark:text-yellow-300">
							{$i18n.t(
								'The following users have access to this knowledge base but do not have access to the {{source}} files you are adding:',
								{ source: conflict.source_type || 'source' }
							)}
						</p>

						<div class="mt-3 space-y-2 max-h-48 overflow-y-auto">
							{#each (conflict.user_details || []).slice(0, 5) as rec}
								<div class="flex items-center justify-between text-sm py-1">
									<div>
										<span class="font-medium">{rec.user_name}</span>
										<span class="text-gray-500 ml-2">{rec.user_email}</span>
									</div>
									{#if rec.grant_access_url}
										<a
											href={rec.grant_access_url}
											target="_blank"
											rel="noopener noreferrer"
											class="text-blue-600 hover:underline text-xs"
										>
											{$i18n.t('Grant access')} ↗
										</a>
									{/if}
								</div>
							{/each}
							{#if (conflict.user_details || []).length > 5}
								<div class="text-sm text-gray-500">
									{$i18n.t('And {{count}} more...', {
										count: (conflict.user_details || []).length - 5
									})}
								</div>
							{/if}
						</div>
					</div>
				{/if}

				<!-- Group-level conflicts -->
				{#if hasGroupConflicts}
					<div class="mb-4 p-3 bg-red-50 dark:bg-red-900/20 rounded-lg">
						<div class="flex items-center gap-2 text-red-800 dark:text-red-200 mb-2">
							<svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
								<path
									stroke-linecap="round"
									stroke-linejoin="round"
									stroke-width="2"
									d="M18.364 18.364A9 9 0 005.636 5.636m12.728 12.728A9 9 0 015.636 5.636m12.728 12.728L5.636 5.636"
								/>
							</svg>
							<span class="font-medium">
								{$i18n.t('Groups with members lacking source access')}
							</span>
						</div>

						<div class="mt-2 space-y-3 max-h-60 overflow-y-auto">
							{#each conflict?.group_conflicts ?? [] as gc}
								<div class="border border-red-200 dark:border-red-800 rounded-lg p-2">
									<div class="flex items-center gap-2 mb-1">
										<span class="font-medium text-sm">{gc.group_name}</span>
										<span class="text-xs px-1.5 py-0.5 bg-gray-100 dark:bg-gray-700 rounded">
											{gc.role}
										</span>
									</div>
									<div class="space-y-1">
										{#each gc.members_without_access as member}
											<div class="flex items-center justify-between text-sm pl-2">
												<div>
													<span>{member.user_name}</span>
													<span class="text-gray-500 ml-2">{member.user_email}</span>
												</div>
												{#if member.grant_access_url}
													<a
														href={member.grant_access_url}
														target="_blank"
														rel="noopener noreferrer"
														class="text-blue-600 hover:underline text-xs"
													>
														{$i18n.t('Grant access')} ↗
													</a>
												{/if}
											</div>
										{/each}
									</div>
								</div>
							{/each}
						</div>

						<p class="text-sm text-red-700 dark:text-red-300 mt-3">
							{$i18n.t(
								'Remove these groups from sharing, or grant source access to all their members before adding these files.'
							)}
						</p>
					</div>
				{/if}

				<!-- Grant access link for public KB -->
				{#if conflict.kb_is_public && conflict.grant_access_url}
					<div class="mb-4 p-3 bg-blue-50 dark:bg-blue-900/20 rounded-lg">
						<a
							href={conflict.grant_access_url}
							target="_blank"
							rel="noopener noreferrer"
							class="text-blue-600 hover:underline text-sm flex items-center gap-1"
						>
							<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
								<path
									stroke-linecap="round"
									stroke-linejoin="round"
									stroke-width="2"
									d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14"
								/>
							</svg>
							{$i18n.t('Open {{source}} to manage permissions', {
								source: conflict.source_type || 'source'
							})}
						</a>
					</div>
				{/if}

				<!-- Actions -->
				<div class="flex justify-end gap-3 mt-4">
					<button
						class="px-4 py-2 text-sm font-medium text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700 rounded-lg"
						on:click={handleCancel}
					>
						{hasGroupConflicts ? $i18n.t('Go Back') : $i18n.t('Cancel')}
					</button>
					{#if !hasGroupConflicts}
						{#if !strictMode && !conflict.kb_is_public}
							<button
								class="px-4 py-2 text-sm font-medium text-yellow-700 dark:text-yellow-300 bg-yellow-100 dark:bg-yellow-900/30 hover:bg-yellow-200 dark:hover:bg-yellow-900/50 rounded-lg"
								on:click={handleContinue}
							>
								{$i18n.t('Continue Anyway')}
							</button>
						{/if}
						{#if conflict.kb_is_public}
							<button
								class="px-4 py-2 text-sm font-medium text-white bg-blue-600 hover:bg-blue-700 rounded-lg"
								on:click={handleMakePrivate}
							>
								{$i18n.t('Make Private')}
							</button>
						{/if}
					{/if}
				</div>
			{/if}
		</div>
	</div>
</Modal>
