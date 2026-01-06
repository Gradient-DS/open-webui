<script>
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import { config, models, settings } from '$lib/stores';
	import { isFeatureEnabled } from '$lib/utils/features';
	import { getModels } from '$lib/apis';
	import Models from '$lib/components/workspace/Models.svelte';

	onMount(async () => {
		if (!isFeatureEnabled('models')) {
			goto('/');
			return;
		}
		await Promise.all([
			(async () => {
				models.set(
					await getModels(
						localStorage.token,
						$config?.features?.enable_direct_connections && ($settings?.directConnections ?? null)
					)
				);
			})()
		]);
	});
</script>

{#if $models !== null}
	<Models />
{/if}
