<script lang="ts">
	import { getContext, createEventDispatcher } from 'svelte';
	import Modal from '$lib/components/common/Modal.svelte';
	import XMark from '$lib/components/icons/XMark.svelte';
	import type { ShareValidationResult, SharingRecommendation, GroupConflict } from '$lib/apis/knowledge/permissions';

	const i18n = getContext('i18n');
	const dispatch = createEventDispatcher();

	export let show = false;
	export let validationResult: ShareValidationResult | null = null;
	export let groupConflicts: GroupConflict[] = [];
	export let strictMode = true;
	export let targetName = '';
	export let isGoingPublic = false;

	$: canShareCount = validationResult?.can_share_to_users?.length ?? 0;
	$: cannotShareCount = validationResult?.cannot_share_to_users?.length ?? 0;
	$: totalCount = canShareCount + cannotShareCount;
	$: hasGroupConflicts = groupConflicts.length > 0;

	function handleConfirm() {
		dispatch('confirm', { shareToAll: !strictMode });
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
						{#if isGoingPublic}
							{$i18n.t('Cannot Make Public')}
						{:else if hasGroupConflicts}
							{$i18n.t('Cannot Share to Group')}
						{:else}
							{$i18n.t('Confirm Sharing')}
						{/if}
					</div>
					<div class="text-sm text-gray-500">
						{#if isGoingPublic}
							{$i18n.t('"{{name}}"', { name: targetName })}
						{:else}
							{$i18n.t('Sharing "{{name}}"', { name: targetName })}
						{/if}
					</div>
				</div>
			</div>
			<button class="self-center" on:click={handleCancel}>
				<XMark className={'size-5'} />
			</button>
		</div>

		<div class="w-full px-5 pb-4 dark:text-white">
			{#if isGoingPublic}
				<!-- Making Public Warning -->
				<div class="mb-4 p-3 bg-red-50 dark:bg-red-900/20 rounded-lg">
					<div class="flex items-center gap-2 text-red-800 dark:text-red-200">
						<svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
							<path
								stroke-linecap="round"
								stroke-linejoin="round"
								stroke-width="2"
								d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"
							/>
						</svg>
						<span class="font-medium">
							{$i18n.t('Contains source-restricted files')}
						</span>
					</div>
					<p class="text-sm text-red-700 dark:text-red-300 mt-2">
						{#if strictMode}
							{$i18n.t(
								'This knowledge base contains files from external sources (e.g. OneDrive) with restricted access. Making it public is not allowed because users without source access would be unable to view the documents.'
							)}
						{:else}
							{$i18n.t(
								'This knowledge base contains files from external sources (e.g. OneDrive) with restricted access. Users without source access will see the knowledge base but will be unable to view the documents.'
							)}
						{/if}
					</p>
				</div>

				<!-- Actions for making public -->
				<div class="flex justify-end gap-3">
					<button
						class="px-4 py-2 text-sm font-medium text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700 rounded-lg"
						on:click={handleCancel}
					>
						{$i18n.t('Keep Private')}
					</button>
					{#if !strictMode}
						<button
							class="px-4 py-2 text-sm font-medium text-white bg-yellow-600 hover:bg-yellow-700 rounded-lg"
							on:click={handleConfirm}
						>
							{$i18n.t('Make Public Anyway')}
						</button>
					{/if}
				</div>
			{:else if hasGroupConflicts}
				<!-- Group Conflicts — hard block, clean layout -->
				<div class="mb-4 space-y-3">
					{#each groupConflicts as conflict}
						<div class="border border-red-200 dark:border-red-800/50 rounded-lg p-3">
							<div class="flex items-center gap-2 mb-2">
								<span class="font-medium text-sm">{conflict.group_name}</span>
								<span class="text-xs text-gray-500 uppercase">{conflict.role}</span>
							</div>
							<div class="space-y-1.5">
								{#each conflict.members_without_access as member}
									<div class="flex items-center justify-between text-sm">
										<div>
											<span class="text-gray-700 dark:text-gray-300">{member.user_email}</span>
											<span class="text-gray-500 ml-2">
												{$i18n.t('Missing: {{count}} {{source}} files', {
													count: member.inaccessible_count,
													source: member.source_type
												})}
											</span>
										</div>
										{#if member.grant_access_url}
											<a
												href={member.grant_access_url}
												target="_blank"
												rel="noopener noreferrer"
												class="text-blue-600 hover:underline text-xs shrink-0"
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

				<div class="mb-4 text-sm text-gray-500 dark:text-gray-400">
					{$i18n.t('Remove these groups from sharing, or grant OneDrive access to all their members before sharing.')}
				</div>

				<!-- Only "Go back" button for group conflicts -->
				<div class="flex justify-end">
					<button
						class="px-4 py-2 text-sm font-medium text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700 rounded-lg"
						on:click={handleCancel}
					>
						{$i18n.t('Go Back')}
					</button>
				</div>
			{:else if validationResult}
				<!-- Users with full access -->
				{#if canShareCount > 0}
					<div class="mb-4 p-3 bg-green-50 dark:bg-green-900/20 rounded-lg">
						<div class="flex items-center gap-2 text-green-800 dark:text-green-200">
							<svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
								<path
									stroke-linecap="round"
									stroke-linejoin="round"
									stroke-width="2"
									d="M5 13l4 4L19 7"
								/>
							</svg>
							<span class="font-medium">
								{$i18n.t('{{count}} users with full source access', { count: canShareCount })}
							</span>
						</div>
						<p class="text-sm text-green-700 dark:text-green-300 mt-1">
							{$i18n.t('Have permissions for all source documents')}
						</p>
					</div>
				{/if}

				<!-- Users without access -->
				{#if cannotShareCount > 0}
					<div class="mb-4 p-3 bg-yellow-50 dark:bg-yellow-900/20 rounded-lg">
						<div class="flex items-center gap-2 text-yellow-800 dark:text-yellow-200">
							<svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
								<path
									stroke-linecap="round"
									stroke-linejoin="round"
									stroke-width="2"
									d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"
								/>
							</svg>
							<span class="font-medium">
								{#if strictMode}
									{$i18n.t('{{count}} users will NOT receive access', { count: cannotShareCount })}
								{:else}
									{$i18n.t('{{count}} users missing source access', { count: cannotShareCount })}
								{/if}
							</span>
						</div>

						<div class="mt-3 space-y-2 max-h-48 overflow-y-auto">
							{#each (validationResult.recommendations || []).slice(0, 5) as rec}
								<div class="flex items-center justify-between text-sm py-1">
									<div>
										<span class="font-medium">{rec.user_email}</span>
										<span class="text-gray-500 ml-2">
											{$i18n.t('Missing: {{count}} {{source}} files', {
												count: rec.inaccessible_count,
												source: rec.source_type
											})}
										</span>
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
							{#if (validationResult.recommendations || []).length > 5}
								<div class="text-sm text-gray-500">
									{$i18n.t('And {{count}} more...', {
										count: (validationResult.recommendations || []).length - 5
									})}
								</div>
							{/if}
						</div>
					</div>
				{/if}

				<!-- Summary -->
				<div class="mb-6 p-3 bg-gray-50 dark:bg-gray-800 rounded-lg text-sm">
					{#if strictMode}
						<p>
							{$i18n.t('Sharing to {{count}} users with source access.', { count: canShareCount })}
						</p>
						{#if cannotShareCount > 0}
							<p class="text-gray-500 mt-1">
								{$i18n.t('{{count}} users excluded - you can reshare once they have access.', {
									count: cannotShareCount
								})}
							</p>
						{/if}
					{:else}
						<p class="text-yellow-700 dark:text-yellow-300">
							{$i18n.t(
								"Warning: {{count}} users don't have access to all source documents. They will see limited content.",
								{ count: cannotShareCount }
							)}
						</p>
					{/if}
					<p class="text-gray-500 mt-2">
						{$i18n.t('Note: Users will lose access if their source permissions are revoked.')}
					</p>
				</div>

				<!-- Actions -->
				<div class="flex justify-end gap-3">
					<button
						class="px-4 py-2 text-sm font-medium text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700 rounded-lg"
						on:click={handleCancel}
					>
						{$i18n.t('Cancel')}
					</button>
					<button
						class="px-4 py-2 text-sm font-medium text-white bg-blue-600 hover:bg-blue-700 rounded-lg"
						on:click={handleConfirm}
					>
						{#if strictMode && cannotShareCount > 0}
							{$i18n.t('Share to {{count}} users', { count: canShareCount })}
						{:else}
							{$i18n.t('Share to all {{count}} users', { count: totalCount })}
						{/if}
					</button>
				</div>
			{/if}
		</div>
	</div>
</Modal>
