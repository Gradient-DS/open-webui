<script>
<<<<<<< HEAD
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import { page } from '$app/stores';
	import {
		isAdminSettingsEnabled,
		isAdminSettingsTabEnabled,
		getFirstAvailableAdminSettingsTab
	} from '$lib/utils/features';
	import Settings from '$lib/components/admin/Settings.svelte';

	onMount(() => {
		// Check if admin settings is disabled entirely
		if (!isAdminSettingsEnabled()) {
			goto('/admin');
			return;
		}

		// Check if the specific tab is disabled
		const tab = $page.params.tab;
		if (!isAdminSettingsTabEnabled(tab)) {
			const firstTab = getFirstAvailableAdminSettingsTab();
			if (firstTab) {
				goto(`/admin/settings/${firstTab}`);
			} else {
				goto('/admin');
			}
		}
	});
</script>
=======
	import { goto } from '$app/navigation';
	import { page } from '$app/stores';
	import { onMount } from 'svelte';
>>>>>>> upstream/main

	onMount(() => {
		const params = new URLSearchParams($page.url.searchParams);
		params.set('settings', `admin:${$page.params.tab ?? 'general'}`);
		goto(`/?${params.toString()}`, { replaceState: true });
	});
</script>
