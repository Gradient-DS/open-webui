<script lang="ts">
	// [Gradient] Vergadering: the "…" menu, in the same shape as the Notes menu (download, delete).
	import { getContext } from 'svelte';
	import type { Writable } from 'svelte/store';
	import type { i18n as i18nType } from 'i18next';

	import Dropdown from '$lib/components/common/Dropdown.svelte';
	import DropdownMenu from '$lib/components/common/DropdownMenu.svelte';
	import DropdownSub from '$lib/components/common/DropdownSub.svelte';
	import Download from '$lib/components/icons/Download.svelte';
	import GarbageBin from '$lib/components/icons/GarbageBin.svelte';
	import ChatBubble from '$lib/components/icons/ChatBubble.svelte';

	const i18n: Writable<i18nType> = getContext('i18n');

	export let show = false;
	export let onDownload: ((format: 'md' | 'docx' | 'pdf') => void) | null = null;
	export let downloadLabel = '';
	export let onDelete: (() => void) | null = null;
	export let onChat: (() => void) | null = null;
	export let onChange: (state: boolean) => void = () => {};

	const itemClass =
		'select-none flex h-[1.6875rem] w-full cursor-pointer items-center gap-2 rounded-xl bg-transparent px-2 text-[0.8125rem] hover:text-gray-900 dark:hover:text-gray-100';
</script>

<Dropdown bind:show align="end" sideOffset={6} onOpenChange={(state) => onChange(state)}>
	<slot />

	<div slot="content">
		<DropdownMenu className="min-w-[11.25rem]">
			{#if onDownload}
				<DropdownSub contentClass="select-none z-50">
					<button slot="trigger" class={itemClass}>
						<Download className="size-3.5" strokeWidth="2" />
						<div class="flex items-center line-clamp-1">
							{downloadLabel ? `${$i18n.t('Download')}: ${downloadLabel}` : $i18n.t('Download')}
						</div>
					</button>

					{#each [['md', $i18n.t('Markdown (.md)')], ['docx', $i18n.t('Word document (.docx)')], ['pdf', $i18n.t('PDF document (.pdf)')]] as [format, label] (format)}
						<button
							class={itemClass}
							on:click={() => {
								onDownload?.(format as 'md' | 'docx' | 'pdf');
								show = false;
							}}
						>
							<div class="flex items-center line-clamp-1">{label}</div>
						</button>
					{/each}
				</DropdownSub>
			{/if}

			{#if onChat}
				<button
					class={itemClass}
					on:click={() => {
						onChat?.();
						show = false;
					}}
				>
					<ChatBubble className="size-3.5" strokeWidth="2" />
					<div class="flex items-center">{$i18n.t('Chat about this meeting')}</div>
				</button>
			{/if}

			{#if onDelete}
				<button
					class={itemClass}
					on:click={() => {
						onDelete?.();
						show = false;
					}}
				>
					<GarbageBin className="size-3.5" strokeWidth="2" />
					<div class="flex items-center">{$i18n.t('Delete')}</div>
				</button>
			{/if}
		</DropdownMenu>
	</div>
</Dropdown>
