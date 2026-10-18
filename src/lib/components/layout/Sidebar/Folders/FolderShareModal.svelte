<script lang="ts">
	import { getContext } from 'svelte';
	const i18n = getContext('i18n');

	import Modal from '$lib/components/common/Modal.svelte';
	import AccessControl from '$lib/components/workspace/common/AccessControl.svelte';
	import XMark from '../icons/XMark.svelte';
	import ExclamationTriangle from '$lib/components/icons/ExclamationTriangle.svelte';
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
			if (freshFolder?.user_id === $user?.id) {
				const sharing = await getFolderSharingById(localStorage.token, folder.id);
				sharesKnowledge = sharing?.shares_knowledge ?? false;
				leftOut = sharing?.left_out ?? [];
			}
		} catch (e) {
			console.error('Failed to load folder access grants', e);
			accessGrants = folder?.access_grants ?? [];
		} finally {
			loading = false;
		}
	};

	const handleAccessChange = async () => {
		if (!folder) return;
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
			{#if sharesKnowledge}
				<div class="mb-3 text-xs text-gray-500 dark:text-gray-400">
					{$i18n.t('Sharing this folder also shares the knowledge bases, files and notes in it.')}
				</div>
			{/if}
			{#if leftOut.length > 0}
				<div
					class="mb-3 flex gap-2 rounded-xl border border-yellow-500/30 bg-yellow-500/10 px-3 py-2 text-xs text-yellow-800 dark:text-yellow-200"
				>
					<ExclamationTriangle className="size-4 shrink-0 mt-0.5" />
					<div>
						<div>{$i18n.t('These items stay private:')}</div>
						<ul class="mt-1 list-disc pl-4">
							{#each leftOut as item (item.type + item.id)}
								<li>
									<span class="font-medium">{item.name}</span>:
									{item.reason === 'cloud'
										? $i18n.t('synced from the cloud, never shared')
										: $i18n.t("someone else's, only its owner can share it")}
								</li>
							{/each}
						</ul>
					</div>
				</div>
			{/if}
			<AccessControl
				bind:accessGrants
				onChange={handleAccessChange}
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
