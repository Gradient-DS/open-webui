<script>
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import { isFeatureEnabled } from '$lib/utils/features';
	import { config } from '$lib/stores';

	import Tools from '$lib/components/workspace/Tools.svelte';

	onMount(() => {
		// [Gradient] Tenant gates also apply to administrators.
		if (!isFeatureEnabled('tools')) {
			goto('/');
		} else if (!$config?.features?.enable_plugins) {
			goto('/workspace', { replaceState: true });
		}
	});
</script>

{#if $config?.features?.enable_plugins}
	<Tools />
{/if}
