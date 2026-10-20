<script lang="ts">
	// [Gradient] The global system prompt: one for every base model without its own (soev/system_prompt.py).
	import type { i18n as i18nType } from 'i18next';
	import { getContext, onMount } from 'svelte';
	import type { Writable } from 'svelte/store';
	import { toast } from 'svelte-sonner';

	import { getSystemPrompt, setSystemPrompt } from '$lib/apis/configs';
	import Textarea from '$lib/components/common/Textarea.svelte';

	const i18n = getContext<Writable<i18nType>>('i18n');

	let { saveHandler }: { saveHandler: () => void } = $props();

	let prompt: string | null = $state(null);
	let saving = $state(false);

	const save = async () => {
		saving = true;
		try {
			prompt = (await setSystemPrompt(localStorage.token, prompt ?? '')).SYSTEM_PROMPT;
			saveHandler();
		} catch (error) {
			toast.error(`${error ?? $i18n.t('Failed to update settings')}`);
		} finally {
			saving = false;
		}
	};

	onMount(async () => {
		try {
			prompt = (await getSystemPrompt(localStorage.token)).SYSTEM_PROMPT;
		} catch (error) {
			toast.error(`${error}`);
		}
	});
</script>

{#if prompt !== null}
	<form
		class="flex flex-col h-full justify-between text-sm"
		onsubmit={(event) => {
			event.preventDefault();
			save();
		}}
	>
		<div class="overflow-y-scroll scrollbar-hidden h-full">
			<div class="mb-2 text-xs font-medium">{$i18n.t('System Prompt')}</div>
			<div class="mb-2.5 text-xs text-gray-500 dark:text-gray-400">
				{$i18n.t(
					'Every model uses this system prompt, unless it still has a prompt of its own. Assistants keep their own instructions.'
				)}
			</div>
			<Textarea
				bind:value={prompt}
				rows={12}
				minSize={240}
				ariaLabel={$i18n.t('System Prompt')}
				placeholder={$i18n.t(
					'Write the system prompt here\ne.g. You are the assistant of our organisation. Answer in the language of the question.'
				)}
			/>
		</div>

		<div class="flex justify-end pt-3">
			<button
				class="px-3.5 py-1.5 text-sm font-medium bg-black hover:bg-gray-900 text-white dark:bg-white dark:text-black dark:hover:bg-gray-100 transition rounded-full disabled:opacity-50"
				type="submit"
				disabled={saving}
			>
				{$i18n.t('Save')}
			</button>
		</div>
	</form>
{/if}
