<script lang="ts">
	// [Gradient] Vergadering: only when FEATURE_MEETINGS is on and soev-api offers the meeting agent.
	import { getContext, onMount } from 'svelte';
	import type { Writable } from 'svelte/store';
	import type { i18n as i18nType } from 'i18next';

	import { goto } from '$app/navigation';
	import { page } from '$app/stores';
	import { WEBUI_NAME } from '$lib/stores';
	import { checkMeetingsAvailable, meetingsAvailable } from '$lib/components/meetings/availability';
	import { refreshMeetings } from '$lib/components/meetings/list';
	import { isFeatureEnabled } from '$lib/utils/features';
	import Spinner from '$lib/components/common/Spinner.svelte';

	let { children } = $props();

	const i18n: Writable<i18nType> = getContext('i18n');
	// The sidebar already ran the check on this page load; only a direct load waits for it.
	let loaded = $state($meetingsAvailable);

	onMount(async () => {
		// The list loads alongside the check instead of after it.
		if (!loaded && isFeatureEnabled('meetings') && $page.route.id === '/(app)/meetings')
			refreshMeetings(localStorage.token).catch(() => {});
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
