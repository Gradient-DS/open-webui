<script lang="ts">
	// [Gradient] Assistants are chosen in the panel; only empty chats can clear the choice.
	import { getContext } from 'svelte';
	import type { Writable } from 'svelte/store';
	import type { i18n as I18n } from 'i18next';
	import { activeAssistant, activeAssistantId } from '$lib/stores/assistant';
	import { WEBUI_API_BASE_URL } from '$lib/constants';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import XMark from '$lib/components/icons/XMark.svelte';

	const i18n = getContext<Writable<I18n>>('i18n');
	export let editable = false;
</script>

{#if $activeAssistant}
	<Tooltip
		content={editable ? $activeAssistant.name : $i18n.t('Start a new chat to switch assistants.')}
	>
		<div
			class="flex items-center gap-1.5 max-w-[10rem] sm:max-w-[13rem] px-2 py-0.5 rounded-xl text-[0.8125rem] text-gray-600 dark:text-gray-300"
		>
			<img
				src={`${WEBUI_API_BASE_URL}/models/model/profile/image?id=${encodeURIComponent($activeAssistant.id)}&lang=${$i18n.language}`}
				alt=""
				class="size-5 rounded-full object-cover shrink-0"
			/>
			<span class="truncate">{$activeAssistant.name}</span>
			{#if editable}
				<button
					type="button"
					class="shrink-0 rounded p-0.5 hover:bg-gray-100 dark:hover:bg-gray-800"
					aria-label={$i18n.t('Clear assistant')}
					on:click={() => activeAssistantId.set(null)}
				>
					<XMark className="size-3.5" />
				</button>
			{/if}
		</div>
	</Tooltip>
{/if}
