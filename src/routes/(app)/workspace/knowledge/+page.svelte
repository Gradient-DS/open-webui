<script>
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import { isFeatureEnabled } from '$lib/utils/features';
	import Knowledge from '$lib/components/workspace/Knowledge.svelte';

	onMount(() => {
		if (!isFeatureEnabled('knowledge')) {
			goto('/');
		}
	});
</script>

<Knowledge />
