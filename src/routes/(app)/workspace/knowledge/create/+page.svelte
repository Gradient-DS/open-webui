<script>
<<<<<<< HEAD
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import { isFeatureEnabled } from '$lib/utils/features';
	import CreateKnowledgeBase from '$lib/components/workspace/Knowledge/CreateKnowledgeBase.svelte';

	onMount(() => {
		if (!isFeatureEnabled('knowledge')) {
			goto('/');
		}
	});
=======
	import Knowledge from '$lib/components/workspace/Knowledge.svelte';
>>>>>>> upstream/main
</script>

<Knowledge showCreateOnMount={true} createModalCloseHref="/workspace/knowledge" />
