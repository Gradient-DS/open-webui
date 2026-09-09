<script lang="ts">
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import { page } from '$app/stores';
	import {
		isAdminSettingsEnabled,
		isAdminSettingsTabEnabled,
		getFirstAvailableAdminSettingsTab
	} from '$lib/utils/features';
	// [Gradient] Legacy admin routes open the gated settings modal.
	onMount(() => {
		if (!isAdminSettingsEnabled()) {
			goto('/admin', { replaceState: true });
			return;
		}
		const requested = '';
		const tab =
			requested && isAdminSettingsTabEnabled(requested)
				? requested
				: getFirstAvailableAdminSettingsTab();
		goto(tab ? '/?settings=admin:' + tab : '/admin', { replaceState: true });
	});
</script>
