<script lang="ts">
	import { marked } from 'marked';

	import { config } from '$lib/stores';
	import { WEBUI_API_BASE_URL } from '$lib/constants';
	import { sanitizeResponseContent } from '$lib/utils';

	let message: string | null = null;
	let fetchedFor: boolean | null = null;

	$: enabled = $config?.features?.enable_welcome_message === true;

	$: if (enabled && fetchedFor !== true) {
		fetchedFor = true;
		fetchMessage();
	} else if (!enabled && fetchedFor !== false) {
		fetchedFor = false;
		message = null;
	}

	async function fetchMessage() {
		try {
			const token = localStorage.token;
			if (!token) return;
			const res = await fetch(`${WEBUI_API_BASE_URL}/agent/gradient_agent_meta`, {
				headers: { Accept: 'application/json', authorization: `Bearer ${token}` }
			});
			if (!res.ok) return;
			const agent = await res.json();
			const value = agent?.config?.welcome_message;
			message = typeof value === 'string' && value.trim() ? value : null;
		} catch (err) {
			console.warn('[WelcomeMessage] fetch failed', err);
			message = null;
		}
	}
</script>

{#if enabled && message}
	<div
		class="mx-auto w-full max-w-3xl px-4 py-3 my-3 rounded-xl border border-gray-100 dark:border-gray-800 bg-gray-50 dark:bg-gray-900/40 text-gray-700 dark:text-gray-200 markdown-prose-sm [&_p]:my-2"
	>
		{@html marked.parse(sanitizeResponseContent(message), { breaks: true })}
	</div>
{/if}
