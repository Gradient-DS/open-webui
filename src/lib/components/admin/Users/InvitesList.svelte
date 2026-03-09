<script lang="ts">
	import { onMount, getContext, createEventDispatcher } from 'svelte';
	import { toast } from 'svelte-sonner';

	import { listInvites, resendInvite, revokeInvite } from '$lib/apis/invites';
	import { config } from '$lib/stores';

	import Spinner from '$lib/components/common/Spinner.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import Plus from '$lib/components/icons/Plus.svelte';

	const i18n = getContext('i18n');
	const dispatch = createEventDispatcher();

	let invites: any[] = [];
	let loading = true;

	export const loadInvites = async () => {
		loading = true;
		try {
			invites = (await listInvites(localStorage.token)) ?? [];
		} catch (err) {
			toast.error(`${err}`);
		}
		loading = false;
	};

	onMount(loadInvites);

	const handleResend = async (inviteId: string) => {
		try {
			const res = await resendInvite(localStorage.token, inviteId);
			if (res) {
				if (res.email_sent) {
					toast.success($i18n.t('Invite sent'));
				} else {
					toast.info($i18n.t('Invite refreshed but email could not be sent'));
				}
				await loadInvites();
			}
		} catch (err) {
			toast.error(`${err}`);
		}
	};

	const handleRevoke = async (inviteId: string) => {
		try {
			await revokeInvite(localStorage.token, inviteId);
			toast.success($i18n.t('Invite revoked'));
			await loadInvites();
		} catch (err) {
			toast.error(`${err}`);
		}
	};

	const formatDate = (epoch: number) => {
		return new Date(epoch * 1000).toLocaleDateString(undefined, {
			month: 'short',
			day: 'numeric',
			hour: '2-digit',
			minute: '2-digit'
		});
	};

	const isExpired = (expiresAt: number) => {
		return expiresAt < Math.floor(Date.now() / 1000);
	};
</script>

<div class="mt-0.5 mb-2 gap-1 flex flex-col md:flex-row justify-between">
	<div class="flex md:self-center text-lg font-medium px-0.5">
		{$i18n.t('Pending Invites')}
		<div class="flex self-center w-[1px] h-6 mx-2.5 bg-gray-200 dark:bg-gray-700" />
		<span class="text-lg font-medium text-gray-500 dark:text-gray-300">{invites.length}</span>
	</div>
	<div>
		<Tooltip content={$i18n.t('Invite User')}>
			<button
				class="p-2 rounded-xl hover:bg-gray-100 dark:bg-gray-900 dark:hover:bg-gray-850 transition font-medium text-sm flex items-center space-x-1"
				on:click={() => dispatch('invite')}
			>
				<Plus className="size-3.5" />
			</button>
		</Tooltip>
	</div>
</div>

{#if loading}
	<div class="flex justify-center py-8">
		<Spinner />
	</div>
{:else if invites.length === 0}
	<div class="flex flex-col items-center justify-center py-16 text-center">
		<div class="mb-4">
			<svg
				xmlns="http://www.w3.org/2000/svg"
				viewBox="0 0 24 24"
				fill="currentColor"
				class="size-12 text-gray-300 dark:text-gray-600"
			>
				<path
					d="M1.5 8.67v8.58a3 3 0 0 0 3 3h15a3 3 0 0 0 3-3V8.67l-8.928 5.493a3 3 0 0 1-3.144 0L1.5 8.67Z"
				/>
				<path
					d="M22.5 6.908V6.75a3 3 0 0 0-3-3h-15a3 3 0 0 0-3 3v.158l9.714 5.978a1.5 1.5 0 0 0 1.572 0L22.5 6.908Z"
				/>
			</svg>
		</div>
		<p class="text-sm text-gray-500 dark:text-gray-400 mb-4">
			{$i18n.t('No pending invites')}
		</p>
		<button
			class="px-4 py-2 text-sm font-medium bg-black hover:bg-gray-900 text-white dark:bg-white dark:text-black dark:hover:bg-gray-100 transition rounded-full flex items-center gap-2"
			on:click={() => dispatch('invite')}
		>
			<Plus className="size-4" />
			{$i18n.t('Invite User')}
		</button>
	</div>
{:else}
	<div class="scrollbar-hidden relative whitespace-nowrap overflow-x-auto max-w-full">
		<table class="w-full text-sm text-left text-gray-500 dark:text-gray-400 table-auto">
			<thead
				class="text-xs text-gray-700 uppercase bg-gray-50 dark:bg-gray-850 dark:text-gray-400"
			>
				<tr>
					<th scope="col" class="px-3 py-2">{$i18n.t('Email')}</th>
					<th scope="col" class="px-3 py-2">{$i18n.t('Name')}</th>
					<th scope="col" class="px-3 py-2">{$i18n.t('Role')}</th>
					<th scope="col" class="px-3 py-2">{$i18n.t('Invited By')}</th>
					<th scope="col" class="px-3 py-2">{$i18n.t('Created')}</th>
					<th scope="col" class="px-3 py-2">{$i18n.t('Expires')}</th>
					<th scope="col" class="px-3 py-2">{$i18n.t('Actions')}</th>
				</tr>
			</thead>
			<tbody>
				{#each invites as invite}
					<tr class="bg-white dark:bg-gray-900 dark:border-gray-850 text-xs">
						<td class="px-3 py-2 font-medium text-gray-900 dark:text-white">
							{invite.email}
						</td>
						<td class="px-3 py-2">{invite.name}</td>
						<td class="px-3 py-2 capitalize">{invite.role}</td>
						<td class="px-3 py-2">{invite.invited_by_name}</td>
						<td class="px-3 py-2">{formatDate(invite.created_at)}</td>
						<td class="px-3 py-2">
							{#if isExpired(invite.expires_at)}
								<span class="text-red-500">{$i18n.t('Expired')}</span>
							{:else}
								{formatDate(invite.expires_at)}
							{/if}
						</td>
						<td class="px-3 py-2">
							<div class="flex gap-1.5">
								{#if $config?.features?.enable_email_invites}
									<button
										class="px-2 py-0.5 text-xs font-medium border border-gray-200 dark:border-gray-700 hover:bg-gray-100 dark:hover:bg-gray-800 rounded transition"
										on:click={() => handleResend(invite.id)}
									>
										{$i18n.t('Resend')}
									</button>
								{/if}
								<button
									class="px-2 py-0.5 text-xs font-medium border border-red-200 dark:border-red-800 text-red-600 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-900/20 rounded transition"
									on:click={() => handleRevoke(invite.id)}
								>
									{$i18n.t('Revoke')}
								</button>
							</div>
						</td>
					</tr>
				{/each}
			</tbody>
		</table>
	</div>
{/if}
