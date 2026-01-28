<script lang="ts">
	import { getContext, onMount } from 'svelte';
	import AccessControl from './AccessControl.svelte';
	import ShareConfirmationModal from './ShareConfirmationModal.svelte';
	import {
		validateKnowledgeShare,
		getUsersReadyForAccess,
		type ShareValidationResult,
		type UserAccessStatus
	} from '$lib/apis/knowledge/permissions';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import UserCircleSolid from '$lib/components/icons/UserCircleSolid.svelte';

	const i18n = getContext('i18n');

	// Props passed through to AccessControl
	export let onChange: Function = () => {};
	export let accessRoles = ['read'];
	export let accessControl = {};
	export let share = true;
	export let sharePublic = true;

	// Source permission props
	export let knowledgeId: string | null = null;
	export let knowledgeName: string = 'Knowledge Base';
	export let strictSourcePermissions = true;

	// Validation state
	let validationResult: ShareValidationResult | null = null;
	let showConfirmModal = false;
	let pendingAccessControl: any = null;
	let validating = false;

	// Users ready to add
	let usersReadyForAccess: UserAccessStatus[] = [];
	let loadingUsersReady = false;

	async function validateAndShare(newAccessControl: any) {
		if (!knowledgeId) {
			onChange(newAccessControl);
			return;
		}

		// Skip validation if making private (setting to empty access control)
		const isPrivate = newAccessControl !== null &&
			(newAccessControl?.read?.user_ids?.length ?? 0) === 0 &&
			(newAccessControl?.read?.group_ids?.length ?? 0) === 0;

		if (isPrivate) {
			onChange(newAccessControl);
			return;
		}

		validating = true;
		try {
			const userIds = newAccessControl?.read?.user_ids ?? [];
			const groupIds = newAccessControl?.read?.group_ids ?? [];

			// Only validate if we're adding users/groups
			if (userIds.length === 0 && groupIds.length === 0) {
				onChange(newAccessControl);
				return;
			}

			validationResult = await validateKnowledgeShare(
				localStorage.token,
				knowledgeId,
				userIds,
				groupIds
			);

			if (validationResult?.source_restricted && !validationResult?.can_share) {
				pendingAccessControl = newAccessControl;
				showConfirmModal = true;
			} else {
				onChange(newAccessControl);
			}
		} catch (err) {
			console.error('Validation failed:', err);
			// If validation fails, proceed without blocking
			onChange(newAccessControl);
		} finally {
			validating = false;
		}
	}

	function handleConfirmShare(event: CustomEvent) {
		const { shareToAll } = event.detail;

		if (strictSourcePermissions && validationResult && !shareToAll) {
			// Filter out users who don't have source access
			const allowedUserIds = new Set(validationResult.can_share_to_users);
			const filteredAccessControl = {
				...pendingAccessControl,
				read: {
					...pendingAccessControl.read,
					user_ids: (pendingAccessControl.read?.user_ids ?? []).filter((id: string) =>
						allowedUserIds.has(id)
					)
				}
			};
			onChange(filteredAccessControl);
		} else {
			onChange(pendingAccessControl);
		}

		showConfirmModal = false;
		pendingAccessControl = null;
		validationResult = null;
	}

	function handleCancelShare() {
		showConfirmModal = false;
		pendingAccessControl = null;
		validationResult = null;
	}

	async function loadUsersReadyForAccess() {
		if (!knowledgeId) return;

		loadingUsersReady = true;
		try {
			usersReadyForAccess = await getUsersReadyForAccess(localStorage.token, knowledgeId);
		} catch (err) {
			console.error('Failed to load users ready for access:', err);
		} finally {
			loadingUsersReady = false;
		}
	}

	function addUserToAccess(userId: string) {
		const newAccessControl = {
			...accessControl,
			read: {
				...(accessControl?.read ?? {}),
				user_ids: [...(accessControl?.read?.user_ids ?? []), userId]
			}
		};
		onChange(newAccessControl);
		usersReadyForAccess = usersReadyForAccess.filter((u) => u.user_id !== userId);
	}

	onMount(async () => {
		await loadUsersReadyForAccess();
	});
</script>

<!-- Original AccessControl with intercepted onChange -->
<AccessControl
	{accessRoles}
	bind:accessControl
	{share}
	{sharePublic}
	onChange={validateAndShare}
/>

<!-- Users Ready to Add Section -->
{#if knowledgeId && usersReadyForAccess.length > 0}
	<div class="mt-4">
		<div class="flex justify-between mb-2.5">
			<div class="text-xs font-medium text-gray-500">
				{$i18n.t('Ready to Add')}
			</div>
			<Tooltip content={$i18n.t("Users with source access who haven't been granted KB access")}>
				<svg class="w-4 h-4 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
					<path
						stroke-linecap="round"
						stroke-linejoin="round"
						stroke-width="2"
						d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"
					/>
				</svg>
			</Tooltip>
		</div>

		<div class="flex flex-col gap-1.5 px-0.5 mx-0.5">
			{#each usersReadyForAccess.slice(0, 5) as user}
				<div class="flex items-center justify-between text-sm py-1">
					<div class="flex items-center gap-2">
						<UserCircleSolid className="w-5 h-5 text-gray-400" />
						<div>
							<span class="font-medium">{user.user_name}</span>
							<span class="text-xs text-gray-500 ml-1">{user.user_email}</span>
						</div>
					</div>
					<button
						class="px-2 py-1 text-xs font-medium text-blue-600 hover:bg-blue-50 dark:hover:bg-blue-900/20 rounded"
						on:click={() => addUserToAccess(user.user_id)}
					>
						{$i18n.t('Add')}
					</button>
				</div>
			{/each}
			{#if usersReadyForAccess.length > 5}
				<div class="text-xs text-gray-500">
					{$i18n.t('And {{count}} more...', { count: usersReadyForAccess.length - 5 })}
				</div>
			{/if}
		</div>
	</div>
{/if}

<!-- Confirmation Modal -->
<ShareConfirmationModal
	bind:show={showConfirmModal}
	{validationResult}
	strictMode={strictSourcePermissions}
	targetName={knowledgeName}
	on:confirm={handleConfirmShare}
	on:cancel={handleCancelShare}
/>
