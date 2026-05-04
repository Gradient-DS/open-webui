<script lang="ts">
	import { onMount } from 'svelte';
	import { marked } from 'marked';
	import { PUBLIC_WELCOME_MESSAGE } from '$env/static/public';

	import { getDefaultAgent } from '$lib/apis/agents';
	import { sanitizeResponseContent } from '$lib/utils';

	const enabled = PUBLIC_WELCOME_MESSAGE === 'true';

	let message: string | null = null;

	onMount(async () => {
		if (!enabled) return;
		try {
			const token = localStorage.token;
			if (!token) return;
			const agent = await getDefaultAgent(token);
			const value = agent?.config?.welcome_message;
			message = typeof value === 'string' && value.trim() ? value : null;
		} catch (err) {
			console.warn('[WelcomeMessage] fetch failed', err);
			message = null;
		}
	});
</script>

{#if enabled && message}
	<div
		class="mx-auto w-full max-w-3xl px-4 py-3 my-3 rounded-xl border border-gray-100 dark:border-gray-800 bg-gray-50 dark:bg-gray-900/40 text-sm text-gray-700 dark:text-gray-200 markdown"
	>
		{@html marked.parse(sanitizeResponseContent(message))}
	</div>
{/if}
