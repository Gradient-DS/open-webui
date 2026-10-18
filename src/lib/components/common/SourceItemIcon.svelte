<script lang="ts">
	// [Gradient] One icon for any knowledge item: the type icon (folder or file)
	// says what it is, a small provider logo at its bottom-right says where it
	// lives. Local items carry no badge.
	import type { ComponentType } from 'svelte';
	import Folder from '$lib/components/icons/Folder.svelte';
	import DocumentPage from '$lib/components/icons/DocumentPage.svelte';
	import { providerBadge } from '$lib/sources/badges';

	export let kind: 'folder' | 'file' = 'file';
	// Overrides the type icon where a list already uses its own (e.g. an open folder).
	export let icon: ComponentType | null = null;
	export let provider: string | null | undefined = null;
	export let className = 'size-3.5';
	export let badgeClassName = 'size-2.5';

	$: badge = providerBadge(provider);
	$: typeIcon = icon ?? (kind === 'folder' ? Folder : DocumentPage);
</script>

<span class="relative inline-flex shrink-0" data-provider={badge ? provider : undefined}>
	<svelte:component this={typeIcon} {className} />
	{#if badge}
		<span
			class="absolute -bottom-1 -right-1 flex items-center justify-center rounded-full bg-white p-px dark:bg-gray-900"
			title={badge.label}
		>
			<svelte:component this={badge.icon} className={badgeClassName} />
		</span>
	{/if}
</span>
