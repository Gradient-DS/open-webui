<script lang="ts">
	import type { RawSourceObject } from './reduceSources';
	import { sourceIcon } from './sourceIcon';

	export let source: RawSourceObject;
	export let className = 'size-4 shrink-0';
	$: icon = sourceIcon(source);
</script>

<span class="{className} inline-flex items-center justify-center" aria-hidden="true">
	{#if icon.component}
		<svelte:component this={icon.component} className="size-full" />
	{:else}
		<img
			src={icon.favicon}
			alt=""
			class="size-full rounded-full"
			on:error|once={(event) => {
				// LICENSE covers this Open WebUI fallback logo.
				// Do not alter, remove, obscure, or replace it except as LICENSE permits:
				// https://docs.openwebui.com/license.
				const image = event.currentTarget as HTMLImageElement;
				image.src = '/favicon.png';
			}}
		/>
	{/if}
</span>
