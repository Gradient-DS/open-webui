<script lang="ts">
	import { decode } from 'html-entities';
	import { slide } from 'svelte/transition';
	import { quintOut } from 'svelte/easing';
	import { getContext } from 'svelte';

	import dayjs from '$lib/dayjs';
	import durationPlugin from 'dayjs/plugin/duration';
	import relativeTime from 'dayjs/plugin/relativeTime';

	dayjs.extend(durationPlugin);
	dayjs.extend(relativeTime);

	import ChevronDown from '$lib/components/icons/ChevronDown.svelte';
	import ChevronUp from '$lib/components/icons/ChevronUp.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import Markdown from '$lib/components/chat/Messages/Markdown.svelte';

	// i18n is provided as a Svelte writable store of the i18next instance.
	// `$i18n.t(...)` auto-subscribes — matches how Collapsible.svelte and the
	// surrounding chat components access translations.
	// eslint-disable-next-line @typescript-eslint/no-explicit-any
	const i18n: any = getContext('i18n');

	export let id = '';
	export let summary: string = '';
	export let body: string = '';
	// Raw <details type="reasoning"> attributes (e.g. { done: "true", duration: "0" }).
	export let attributes: Record<string, string> = {};
	// When true, this bullet is rendering as the StatusHistory dropdown header
	// (inside the outer toggle <button>). Rendered as a plain row — no inner
	// click handler, no chevron — so clicks bubble to the dropdown toggle.
	// When false (the default, used for items inside the expanded bullet list)
	// the bullet has its own button + chevron for expanding the reasoning body.
	export let asHeader: boolean = false;

	// open-webui HTML-escapes the streamed reasoning content (middleware.py:481
	// wraps each line in `> ` then html.escape()s the whole block), so we must
	// decode entities before handing the body to Markdown — otherwise users see
	// literal "&gt;", "&quot;", "&#x27;" in the rendered output.
	$: decodedBody = decode(body);
	$: isDone = attributes?.done === 'true';
	$: durationN = Number(attributes?.duration ?? '0');

	$: label = (() => {
		// Match Collapsible.svelte's i18n keys so existing translations cover us.
		if (!isDone) {
			return summary?.trim().length ? summary : $i18n.t('Thinking...');
		}
		if (durationN < 1) {
			return $i18n.t('Thought for less than a second');
		}
		if (durationN < 60) {
			return $i18n.t('Thought for {{DURATION}} seconds', { DURATION: durationN });
		}
		return $i18n.t('Thought for {{DURATION}}', {
			DURATION: dayjs.duration(durationN, 'seconds').humanize()
		});
	})();

	let open = false;
</script>

<div class="status-description w-full">
	{#if asHeader}
		<div
			class="flex items-center gap-1.5 w-full text-left text-base text-gray-500 dark:text-gray-500"
		>
			{#if !isDone}
				<div class="shrink-0">
					<Spinner className="size-4" />
				</div>
			{/if}
			<span class="line-clamp-1 flex-1 {!isDone ? 'shimmer' : ''}">{label}</span>
		</div>
	{:else}
		<button
			type="button"
			class="flex items-center gap-1.5 w-full text-left text-base text-gray-500 dark:text-gray-500 hover:text-gray-700 dark:hover:text-gray-300 transition"
			on:click|stopPropagation={() => (open = !open)}
		>
			{#if !isDone}
				<div class="shrink-0">
					<Spinner className="size-4" />
				</div>
			{/if}
			<span class="line-clamp-1 flex-1 {!isDone ? 'shimmer' : ''}">{label}</span>
			<span class="flex self-center translate-y-[1px] shrink-0">
				{#if open}
					<ChevronUp strokeWidth="3.5" className="size-3.5" />
				{:else}
					<ChevronDown strokeWidth="3.5" className="size-3.5" />
				{/if}
			</span>
		</button>
		{#if open}
			<div
				transition:slide={{ duration: 200, easing: quintOut, axis: 'y' }}
				class="pl-3 my-1 text-sm text-gray-600 dark:text-gray-300"
			>
				<Markdown id={`${id}-reasoning`} content={decodedBody} />
			</div>
		{/if}
	{/if}
</div>
