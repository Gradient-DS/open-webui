<script lang="ts">
	import { goto } from '$app/navigation';
	import { user } from '$lib/stores';
	import { isFeatureEnabled } from '$lib/utils/features';
	import { onMount } from 'svelte';

	onMount(() => {
		// Find first available workspace section considering both feature flags and permissions
		if (
			isFeatureEnabled('models') &&
			($user?.role === 'admin' || $user?.permissions?.workspace?.models)
		) {
			goto('/workspace/models');
		} else if (
			isFeatureEnabled('knowledge') &&
			($user?.role === 'admin' || $user?.permissions?.workspace?.knowledge)
		) {
			goto('/workspace/knowledge');
		} else if (
			isFeatureEnabled('prompts') &&
			($user?.role === 'admin' || $user?.permissions?.workspace?.prompts)
		) {
			goto('/workspace/prompts');
		} else if (
			isFeatureEnabled('tools') &&
			($user?.role === 'admin' || $user?.permissions?.workspace?.tools)
		) {
			goto('/workspace/tools');
		} else {
			goto('/');
		}
	});
</script>
