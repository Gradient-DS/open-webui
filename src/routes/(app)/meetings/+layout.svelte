<script lang="ts">
	// [Gradient] Vergadering: only when FEATURE_MEETINGS is on and soev-api offers the meeting agent.
	import { getContext, onMount } from 'svelte';
	import type { Writable } from 'svelte/store';
	import type { i18n as i18nType } from 'i18next';

	import { goto } from '$app/navigation';
	import { WEBUI_NAME } from '$lib/stores';
	import { checkMeetingsAvailable } from '$lib/components/meetings/availability';
	import Spinner from '$lib/components/common/Spinner.svelte';

	let { children } = $props();

	const i18n: Writable<i18nType> = getContext('i18n');
	let loaded = $state(false);

	onMount(async () => {
		if (!(await checkMeetingsAvailable(localStorage.token))) {
			goto('/');
			return;
		}
		loaded = true;
	});
</script>

<svelte:head>
	<title>{$i18n.t('Meetings')} / {$WEBUI_NAME}</title>
</svelte:head>

{#if loaded}
	{@render children()}
{:else}
	<div class="w-full h-screen flex justify-center items-center"><Spinner className="size-5" /></div>
{/if}
