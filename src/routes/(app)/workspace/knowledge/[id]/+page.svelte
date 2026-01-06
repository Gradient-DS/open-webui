<script>
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import { isFeatureEnabled } from '$lib/utils/features';
	import KnowledgeBase from '$lib/components/workspace/Knowledge/KnowledgeBase.svelte';

	onMount(() => {
		if (!isFeatureEnabled('knowledge')) {
			goto('/');
		}
	});
</script>

<KnowledgeBase />
