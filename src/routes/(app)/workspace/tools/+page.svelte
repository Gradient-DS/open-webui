<script>
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import { isFeatureEnabled } from '$lib/utils/features';
	import Tools from '$lib/components/workspace/Tools.svelte';

	onMount(() => {
		if (!isFeatureEnabled('tools')) {
			goto('/');
		}
	});
</script>

<Tools />
