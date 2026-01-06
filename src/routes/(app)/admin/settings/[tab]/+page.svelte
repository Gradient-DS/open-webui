<script>
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import { page } from '$app/stores';
	import { isFeatureEnabled } from '$lib/utils/features';
	import Settings from '$lib/components/admin/Settings.svelte';

	onMount(() => {
		if ($page.params.tab === 'audio' && !isFeatureEnabled('voice')) {
			goto('/admin/settings');
		}
	});
</script>

<Settings />
