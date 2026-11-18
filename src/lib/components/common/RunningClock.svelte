<script lang="ts">
	// [Gradient] Time since `since` (epoch seconds), as 0:42 or 1:02:05, ticking every second.
	import { onDestroy } from 'svelte';
	import { clock } from '$lib/utils/clock';

	export let since: number;

	let now = Date.now() / 1000;
	const timer = setInterval(() => (now = Date.now() / 1000), 1000);
	onDestroy(() => clearInterval(timer));
</script>

<span class="tabular-nums">{clock(now - since)}</span>
