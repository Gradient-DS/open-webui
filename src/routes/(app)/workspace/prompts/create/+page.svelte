<script>
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import { isFeatureEnabled } from '$lib/utils/features';
	// [Gradient] Keep disabled prompts unreachable through the modal route.
	onMount(() => {
		if (!isFeatureEnabled('prompts')) goto('/', { replaceState: true });
	});
	import Prompts from '$lib/components/workspace/Prompts.svelte';
</script>

<Prompts showCreateOnMount={true} createModalCloseHref="/workspace/prompts" />
