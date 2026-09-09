<script>
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import { isFeatureEnabled } from '$lib/utils/features';
	import CreateKnowledgeBase from '$lib/components/workspace/Knowledge/CreateKnowledgeBase.svelte';

	onMount(() => {
		if (!isFeatureEnabled('knowledge')) {
			goto('/');
		}
	});
</script>

<CreateKnowledgeBase />
