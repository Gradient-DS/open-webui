<script lang="ts">
	// [Gradient] Text typed out quickly when it changes; at once under reduced motion. The full
	// text holds its space from the start, so only the letters move, never the layout around it.
	import { onDestroy } from 'svelte';

	export let text = '';
	/** The whole text is typed within this many milliseconds, however long it is. */
	export let duration = 400;

	let shown = '';
	let typed: string | null = null;
	let timer: ReturnType<typeof setInterval> | undefined;

	const reduced = () =>
		typeof window !== 'undefined' &&
		window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;

	const type = (full: string) => {
		if (full === typed) return;
		typed = full;
		clearInterval(timer);
		if (reduced() || !full) {
			shown = full;
			return;
		}
		const step = Math.max(1, Math.ceil(full.length / (duration / 12)));
		let length = 0;
		shown = '';
		timer = setInterval(() => {
			length = Math.min(full.length, length + step);
			shown = full.slice(0, length);
			if (length >= full.length) clearInterval(timer);
		}, 12);
	};

	$: type(text);
	onDestroy(() => clearInterval(timer));
</script>

<span class="relative inline-block max-w-full" aria-label={text}>
	<span class="invisible" aria-hidden="true">{text}</span>
	<span class="absolute inset-0" aria-hidden="true">{shown}</span>
</span>
