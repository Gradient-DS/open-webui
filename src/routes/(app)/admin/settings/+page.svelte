<script>
	import { goto } from '$app/navigation';
	import { onMount } from 'svelte';
	import { isAdminSettingsEnabled, getFirstAvailableAdminSettingsTab } from '$lib/utils/features';

	onMount(() => {
		// Check if admin settings is disabled entirely
		if (!isAdminSettingsEnabled()) {
			goto('/admin');
			return;
		}

		// Redirect to first available tab
		const firstTab = getFirstAvailableAdminSettingsTab();
		if (firstTab) {
			goto(`/admin/settings/${firstTab}`);
		} else {
			goto('/admin');
		}
	});
</script>
