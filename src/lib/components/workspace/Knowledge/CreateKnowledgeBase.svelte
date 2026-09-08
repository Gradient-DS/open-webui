<script>
	import { toast } from 'svelte-sonner';

	import { goto } from '$app/navigation';
	import { page } from '$app/stores';
	import { getContext, onMount } from 'svelte';
	const i18n = getContext('i18n');

	import { user, config } from '$lib/stores';
	import { createNewKnowledge } from '$lib/apis/knowledge';

	import AccessControl from '../common/AccessControl.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import XMark from '$lib/components/icons/XMark.svelte';

	export let modal = false;
	/** @type {() => void | Promise<void>} */
	export let onBack = () => goto('/workspace/knowledge');
	/** @type {(knowledge: { id: string }) => void | Promise<void>} */
	export let onCreated = (knowledge) => goto(`/workspace/knowledge/${knowledge.id}`);

	let loading = false;

	let type = $page.url.searchParams.get('type') || 'local';
	// When set (the "+ Add knowledge" builder flow), carry it through to
	// the KB detail page so it can offer a "Back to assistant" return.
	const returnTo = $page.url.searchParams.get('returnTo');

	onMount(() => {
		// The Confluence self-service create flow exists ONLY in per-user
		// ("on request", OAuth) mode. In pre-synced/shared mode the single shared
		// KB is admin-managed and there is no create path for anyone (including
		// admins — they provision it from the Cloud Sync admin panel). Block the
		// route for any non-per_user mode regardless of role.
		if (type === 'confluence' && $config?.features?.confluence_kb_mode !== 'per_user') {
			toast.error($i18n.t('Confluence knowledge bases are managed by administrators.'));
			goto('/workspace/knowledge');
		}
	});

	let name = '';
	let description = '';
	/** @type {{ id?: string; principal_type: 'user' | 'group'; principal_id: string; permission: 'read' | 'write' }[]} */
	let accessGrants = [];

	const submitHandler = async () => {
		loading = true;

		if (name.trim() === '' || description.trim() === '') {
			toast.error($i18n.t('Please fill in all fields.'));
			name = '';
			description = '';
			loading = false;
			return;
		}

		const res = await createNewKnowledge(
			localStorage.token,
			name,
			description,
			type === 'local' ? accessGrants : [],
			type
		).catch((e) => {
			toast.error(`${e}`);
		});

		if (res) {
			toast.success($i18n.t('Knowledge created successfully.'));
<<<<<<< HEAD
			// Preserve returnTo (the builder's "New Knowledge" flow) for
			// every type, alongside any provider auto-sync trigger, so the
			// KB detail page can offer a "Back to assistant" return.
			const params = new URLSearchParams();
			if (type === 'onedrive') {
				params.set('start_onedrive_sync', 'true');
			} else if (type === 'google_drive') {
				params.set('start_google_drive_sync', 'true');
			} else if (type === 'confluence') {
				params.set('start_confluence_sync', 'true');
			}
			if (returnTo) {
				params.set('returnTo', returnTo);
			}
			const qs = params.toString();
			goto(`/workspace/knowledge/${res.id}${qs ? `?${qs}` : ''}`);
=======
			await onCreated(res);
>>>>>>> upstream/main
		}

		loading = false;
	};
</script>

<div class="w-full max-h-full">
	{#if modal}
		<div class="flex justify-between items-center dark:text-gray-100 px-5 pt-4 pb-2">
			<h3 class="text-base font-normal">{$i18n.t('Create a knowledge base')}</h3>
			<button
				class="self-center shrink-0 ml-2"
				aria-label={$i18n.t('Close')}
				type="button"
				on:click={() => {
					onBack();
				}}
			>
				<XMark className="size-5" />
			</button>
		</div>
	{:else}
		<button
			class="flex space-x-1"
			on:click={() => {
				onBack();
			}}
		>
			<div class=" self-center">
				<svg
					xmlns="http://www.w3.org/2000/svg"
					viewBox="0 0 20 20"
					fill="currentColor"
					class="w-4 h-4"
				>
					<path
						fill-rule="evenodd"
						d="M17 10a.75.75 0 01-.75.75H5.612l4.158 3.96a.75.75 0 11-1.04 1.08l-5.5-5.25a.75.75 0 010-1.08l5.5-5.25a.75.75 0 111.04 1.08L5.612 9.25H16.25A.75.75 0 0117 10z"
						clip-rule="evenodd"
					/>
				</svg>
			</div>
			<div class=" self-center font-normal text-sm">{$i18n.t('Back')}</div>
		</button>
	{/if}

	<form
		class="flex flex-col {modal ? 'px-5 pb-3' : 'max-w-lg mx-auto mt-10 mb-10'}"
		on:submit|preventDefault={() => {
			submitHandler();
		}}
	>
		<div class="w-full flex flex-col {modal ? '' : 'justify-center'}">
			{#if !modal}
				<div class=" text-2xl font-normal mb-2.5">
					{$i18n.t('Create a knowledge base')}
				</div>
			{/if}

			<div class="w-full flex flex-col gap-2.5">
				<div class="w-full">
					<div class="{modal ? 'text-xs text-gray-500' : 'text-sm'} mb-2">
						{$i18n.t('What are you working on?')}
					</div>

					<div class="w-full mt-1">
						<input
							class={modal
								? 'w-full text-sm bg-transparent outline-hidden placeholder:text-gray-300 dark:placeholder:text-gray-700'
								: 'w-full rounded-lg py-2 px-4 text-sm bg-gray-50 dark:text-gray-300 dark:bg-gray-850 outline-hidden'}
							type="text"
							bind:value={name}
							placeholder={$i18n.t('Name your knowledge base')}
							required
						/>
					</div>
				</div>

				<div>
					<div class="{modal ? 'text-xs text-gray-500' : 'text-sm'} mb-2">
						{$i18n.t('What are you trying to achieve?')}
					</div>

					<div class="w-full mt-1">
						<textarea
							class={modal
								? 'w-full resize-none text-sm bg-transparent outline-hidden placeholder:text-gray-300 dark:placeholder:text-gray-700'
								: 'w-full resize-none rounded-lg py-2 px-4 text-sm bg-gray-50 dark:text-gray-300 dark:bg-gray-850 outline-hidden'}
							rows={modal ? 6 : 4}
							bind:value={description}
							placeholder={$i18n.t('Describe your knowledge base and objectives')}
							required
						></textarea>
					</div>
				</div>
			</div>
		</div>

		{#if type === 'local'}
			<div class="mt-2">
				<AccessControl
					bind:accessGrants
					accessRoles={['read', 'write']}
					share={$user?.permissions?.sharing?.knowledge || $user?.role === 'admin'}
					sharePublic={$user?.permissions?.sharing?.public_knowledge || $user?.role === 'admin'}
					shareUsers={($user?.permissions?.access_grants?.allow_users ?? true) ||
						$user?.role === 'admin'}
				/>
			</div>
		{/if}

		<div class="flex justify-end {modal ? 'pt-3 gap-2' : 'mt-2'}">
			{#if modal}
				<button
					class="px-3 py-1 text-xs text-gray-500 hover:text-gray-700 dark:hover:text-gray-200 transition"
					type="button"
					on:click={() => {
						onBack();
					}}
				>
					{$i18n.t('Cancel')}
				</button>
			{/if}

			<div>
				<button
					class="{modal
						? `px-3.5 py-1.5 text-sm bg-black hover:bg-gray-900 text-white dark:bg-white dark:text-black dark:hover:bg-gray-100 transition rounded-full ${loading ? 'cursor-not-allowed' : ''}`
						: `text-sm px-4 py-2 transition rounded-lg ${loading ? 'cursor-not-allowed bg-gray-100 dark:bg-gray-800' : 'bg-gray-50 hover:bg-gray-100 dark:bg-gray-850 dark:hover:bg-gray-800'}`} flex"
					type="submit"
					disabled={loading}
				>
					<div class=" self-center font-normal">{$i18n.t('Create Knowledge')}</div>

					{#if loading}
						<div class="ml-1.5 self-center">
							<Spinner />
						</div>
					{/if}
				</button>
			</div>
		</div>
	</form>
</div>
