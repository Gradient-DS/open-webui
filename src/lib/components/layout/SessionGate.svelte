<script lang="ts">
	import type { Snippet } from 'svelte';
	import NoAccess from './NoAccess.svelte';

	let {
		ready = false,
		denied = false,
		children
	}: {
		ready?: boolean;
		denied?: boolean;
		children: Snippet;
	} = $props();
</script>

{#if denied}
	<NoAccess />
{:else if ready}
	{@render children()}
{/if}
