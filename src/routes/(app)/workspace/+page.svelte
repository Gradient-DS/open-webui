<script lang="ts">
	import { goto } from '$app/navigation';
<<<<<<< HEAD
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
		} else if (
			isFeatureEnabled('skills') &&
			($user?.role === 'admin' || $user?.permissions?.workspace?.skills)
		) {
			goto('/workspace/skills');
		} else {
			goto('/');
=======
	import { config, user } from '$lib/stores';
	import { onMount } from 'svelte';

	onMount(() => {
		if ($user?.role !== 'admin') {
			if ($user?.permissions?.workspace?.models) {
				goto('/workspace/models', { replaceState: true });
			} else if ($user?.permissions?.workspace?.knowledge) {
				goto('/workspace/knowledge', { replaceState: true });
			} else if ($user?.permissions?.workspace?.prompts) {
				goto('/workspace/prompts', { replaceState: true });
			} else if ($config?.features?.enable_plugins && $user?.permissions?.workspace?.tools) {
				goto('/workspace/tools', { replaceState: true });
			} else if ($user?.permissions?.workspace?.skills) {
				goto('/workspace/skills', { replaceState: true });
			} else {
				goto('/', { replaceState: true });
			}
		} else {
			goto('/workspace/models', { replaceState: true });
>>>>>>> upstream/main
		}
	});
</script>
