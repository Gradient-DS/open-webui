<script lang="ts">
	import { getContext, onMount } from 'svelte';
	import type { Writable } from 'svelte/store';
	import type { i18n as I18n } from 'i18next';
	import { getGroupUsers } from '$lib/apis/groups';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import { toast } from 'svelte-sonner';

	const i18n = getContext<Writable<I18n>>('i18n');
	export let groupId: string;
	export let userCount = 0;
	let users: { id: string; name: string; email: string }[] | null = null;
	onMount(async () => {
		try {
			users = await getGroupUsers(localStorage.token, groupId);
			userCount = users?.length ?? 0;
		} catch (error) {
			toast.error(String(error));
			users = [];
		}
	});
</script>

<p class="mb-3 text-sm text-gray-500">{$i18n.t('Groups are managed in the platform console.')}</p>
{#if users === null}
	<Spinner />
{:else}
	<ul class="space-y-2 text-sm">
		{#each users as user (user.id)}
			<li>{user.name} <span class="text-gray-500">{user.email}</span></li>
		{/each}
	</ul>
{/if}
