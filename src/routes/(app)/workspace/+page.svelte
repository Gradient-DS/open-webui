<script lang="ts">
	import { goto } from '$app/navigation';
	import { config, user } from '$lib/stores';
	import { isFeatureEnabled } from '$lib/utils/features';
	import { onMount } from 'svelte';

	onMount(() => {
		// [Gradient] Find first available workspace section considering both feature flags and permissions
		if (
			isFeatureEnabled('models') &&
			($user?.role === 'admin' || $user?.permissions?.workspace?.models)
		) {
			goto('/workspace/models', { replaceState: true });
		} else if (
			isFeatureEnabled('knowledge') &&
			($user?.role === 'admin' || $user?.permissions?.workspace?.knowledge)
		) {
			goto('/workspace/knowledge', { replaceState: true });
		} else if (
			isFeatureEnabled('prompts') &&
			($user?.role === 'admin' || $user?.permissions?.workspace?.prompts)
		) {
			goto('/workspace/prompts', { replaceState: true });
		} else if (
			isFeatureEnabled('tools') &&
			$config?.features?.enable_plugins &&
			($user?.role === 'admin' || $user?.permissions?.workspace?.tools)
		) {
			goto('/workspace/tools', { replaceState: true });
		} else if (
			isFeatureEnabled('skills') &&
			($user?.role === 'admin' || $user?.permissions?.workspace?.skills)
		) {
			goto('/workspace/skills', { replaceState: true });
		} else {
			goto('/', { replaceState: true });
		}
	});
</script>
