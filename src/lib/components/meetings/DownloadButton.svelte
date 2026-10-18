<script lang="ts">
	// [Gradient] Vergadering: the visible Download button of a tab (Markdown / Word / PDF).
	import { getContext } from 'svelte';
	import type { Writable } from 'svelte/store';
	import type { i18n as i18nType } from 'i18next';

	import Dropdown from '$lib/components/common/Dropdown.svelte';
	import DropdownMenu from '$lib/components/common/DropdownMenu.svelte';
	import ArrowDownTray from '$lib/components/icons/ArrowDownTray.svelte';

	const i18n: Writable<i18nType> = getContext('i18n');

	export let onDownload: (format: 'md' | 'docx' | 'pdf') => void;
	export let className = '';

	let show = false;

	const itemClass =
		'select-none flex h-[1.6875rem] w-full cursor-pointer items-center gap-2 rounded-xl bg-transparent px-2 text-[0.8125rem] hover:text-gray-900 dark:hover:text-gray-100';
</script>

<Dropdown bind:show align="end" sideOffset={6}>
	<button type="button" class={className} aria-label={$i18n.t('Download')}>
		<ArrowDownTray className="size-3" strokeWidth="2" />
		{$i18n.t('Download')}
	</button>

	<div slot="content">
		<DropdownMenu className="min-w-[10rem]">
			{#each [['md', $i18n.t('Markdown (.md)')], ['docx', $i18n.t('Word document (.docx)')], ['pdf', $i18n.t('PDF document (.pdf)')]] as [format, label] (format)}
				<button
					class={itemClass}
					on:click={() => {
						onDownload(format as 'md' | 'docx' | 'pdf');
						show = false;
					}}
				>
					<div class="flex items-center line-clamp-1">{label}</div>
				</button>
			{/each}
		</DropdownMenu>
	</div>
</Dropdown>
