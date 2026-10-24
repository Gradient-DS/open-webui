<script lang="ts">
	import { getContext } from 'svelte';
	const i18n = getContext<import('svelte/store').Writable<import('i18next').i18n>>('i18n');

	import Modal from '$lib/components/common/Modal.svelte';
	import AccessControl from './AccessControl.svelte';
	import LeftOutList from './LeftOutList.svelte';
	import { getModelSharing } from '$lib/apis/models';
	import XMark from '$lib/components/icons/XMark.svelte';

	type AccessGrant = {
		id?: string;
		principal_type: 'user' | 'group' | 'anyone';
		principal_id: string;
		permission: 'read' | 'write';
	};

	export let modelId: string | null = null;
	let sharing: Awaited<ReturnType<typeof getModelSharing>> | null = null;
	let loadingSharing = false;
	$: if (show && modelId) loadSharing(modelId);

	const loadSharing = async (id: string) => {
		loadingSharing = true;
		sharing = null;
		try {
			const result = await getModelSharing(localStorage.token, id);
			if (id === modelId) sharing = result;
		} catch (error) {
			console.error('Failed to load assistant sharing', error);
		} finally {
			loadingSharing = false;
		}
	};

	export let show = false;
	export let accessGrants: AccessGrant[] = [];
	export let accessControl: any = undefined;
	export let accessRoles = ['read'];

	export let readOnly = false;
	export let share = true;
	export let sharePublic = true;
	export let shareOpen = false;
	export let shareUsers = true;

	export let onChange = () => {};
</script>

<Modal size="sm" bind:show>
	<div>
		<div class="flex justify-between dark:text-gray-100 px-4 pt-3 pb-1">
			<div class="text-base font-normal self-center">
				{$i18n.t('Access Control')}
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

		<div class="w-full px-4 pb-3 dark:text-white">
			{#if modelId && sharing?.shares_knowledge}
				<div class="mb-3 text-xs text-gray-500 dark:text-gray-400">
					{$i18n.t('Sharing this assistant also shares its knowledge bases, files and notes.')}
				</div>
				{#if !sharing.may_share}
					<div class="mb-3 text-xs text-gray-500 dark:text-gray-400">
						{$i18n.t('Only the owner can change who this assistant is shared with.')}
					</div>
				{/if}
				<LeftOutList items={sharing.left_out} />
			{/if}
			<AccessControl
				bind:accessGrants
				bind:accessControl
				{onChange}
				{accessRoles}
				readOnly={readOnly || (!!modelId && (loadingSharing || !sharing?.may_share))}
				{share}
				{sharePublic}
				{shareOpen}
				{shareUsers}
			/>
		</div>
	</div>
</Modal>
