<script lang="ts">
	import { getContext } from 'svelte';
	const i18n = getContext<import('svelte/store').Writable<import('i18next').i18n>>('i18n');

	import Modal from '$lib/components/common/Modal.svelte';
	import AccessControl from '$lib/components/workspace/common/AccessControl.svelte';
	import XMark from '../icons/XMark.svelte';
	import LeftOutList from '$lib/components/workspace/common/LeftOutList.svelte';
	import { getFolderById, getFolderSharingById, updateFolderAccessById } from '$lib/apis/folders';
	import { user } from '$lib/stores';

	type AccessGrant = {
		id?: string;
		principal_type: 'user' | 'group';
		principal_id: string;
		permission: 'read' | 'write';
	};

	export let show = false;
	export let folder: any = null;

	// [Gradient] On v2 sharing a folder shares its knowledge; cloud KBs and others' items stay private.
	type LeftOut = { type: string; id: string; name: string; reason: 'cloud' | 'owner' };

	let accessGrants: AccessGrant[] = [];
	let loading = false;
	let sharesKnowledge = false;
	let leftOut: LeftOut[] = [];
	// [Gradient] Read-only until the server says otherwise: on v2 only the owner changes a folder's access.
	let mayShare = false;

	// Fetch fresh folder data (with access_grants) when modal opens
	$: if (show && folder?.id) {
		loadAccessGrants();
	}

	const loadAccessGrants = async () => {
		loading = true;
		try {
			const freshFolder = await getFolderById(localStorage.token, folder.id);
			if (freshFolder) {
				accessGrants = freshFolder.access_grants ?? [];
			}
			const sharing = await getFolderSharingById(localStorage.token, folder.id);
			sharesKnowledge = sharing?.shares_knowledge ?? false;
			mayShare = sharing?.may_share ?? false;
			leftOut = sharing?.left_out ?? [];
		} catch (e) {
			console.error('Failed to load folder access grants', e);
			accessGrants = folder?.access_grants ?? [];
		} finally {
			loading = false;
		}
	};

	const handleAccessChange = async () => {
		if (!folder || !mayShare) return;
		try {
			const res = await updateFolderAccessById(localStorage.token, folder.id, accessGrants);
			if (res) {
				accessGrants = res.access_grants ?? accessGrants;
			}
		} catch (e) {
			console.error('Failed to update folder access', e);
		}
	};
</script>

<Modal size="sm" bind:show>
	<div>
		<div class=" flex justify-between dark:text-gray-100 px-5 pt-3 pb-1">
			<div class=" text-sm self-center">
				{$i18n.t('Share')}: {folder?.name ?? ''}
			</div>
			<button
				class="self-center rounded-lg p-1 text-gray-500 transition hover:bg-gray-50 hover:text-gray-700 dark:text-gray-400 dark:hover:bg-gray-800 dark:hover:text-gray-200"
				on:click={() => {
					show = false;
				}}
			>
				<XMark className={'size-4'} />
			</button>
		</div>

		<div class="w-full px-5 pb-4 dark:text-white">
			{#if !loading && !mayShare}
				<div class="mb-3 text-xs text-gray-500 dark:text-gray-400">
					{$i18n.t('Only the owner can change who this folder is shared with.')}
				</div>
			{/if}
			{#if sharesKnowledge}
				<div class="mb-3 text-xs text-gray-500 dark:text-gray-400">
					{$i18n.t('Sharing this folder also shares the knowledge bases, files and notes in it.')}
				</div>
			{/if}
			<LeftOutList items={leftOut} />
			<AccessControl
				bind:accessGrants
				onChange={handleAccessChange}
				readOnly={!mayShare}
				accessRoles={['read', 'write']}
				defaultPermission="write"
				share={$user?.role === 'admin' || $user?.permissions?.sharing?.folders}
				sharePublic={false}
				shareUsers={$user?.role === 'admin' || $user?.permissions?.access_grants?.allow_users}
				allowGroups={$user?.role === 'admin' ||
					($user?.permissions?.access_grants?.allow_groups ?? true)}
			/>
		</div>
	</div>
</Modal>
