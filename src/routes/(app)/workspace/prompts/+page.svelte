<script>
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import { isFeatureEnabled } from '$lib/utils/features';
	import Prompts from '$lib/components/workspace/Prompts.svelte';

	onMount(() => {
		if (!isFeatureEnabled('prompts')) {
			goto('/');
		}
	});
</script>

<Prompts />
