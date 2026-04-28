<script lang="ts">
	import ChevronDown from '$lib/components/icons/ChevronDown.svelte';
	import ChevronUp from '$lib/components/icons/ChevronUp.svelte';
	import Search from '$lib/components/icons/Search.svelte';
	import Collapsible from '$lib/components/common/Collapsible.svelte';

	export let status = { urls: [], query: '' };
	let state = false;

	// Favicon stack preview in the collapsed header. Show up to 5 stacked,
	// then a "+N" badge for the rest. Source list mirrors the expanded
	// list: `items[].link` for web_search results, `urls[]` for fetch_url.
	const MAX_PREVIEW = 5;
	$: previewLinks = (
		status?.items
			? status.items.map((it) => it?.link).filter(Boolean)
			: status?.urls ?? []
	) as string[];
	$: previewVisible = previewLinks.slice(0, MAX_PREVIEW);
	$: previewOverflow = Math.max(0, previewLinks.length - MAX_PREVIEW);
</script>

<Collapsible grow={true} className="w-full" buttonClassName="w-full" bind:open={state}>
	<div class="flex items-center gap-2 text-gray-500 transition">
		{#if previewVisible.length > 0}
			<div class="flex items-center -space-x-2 shrink-0" aria-hidden="true">
				{#each previewVisible as link (link)}
					<img
						src="https://www.google.com/s2/favicons?sz=32&domain={link}"
						alt=""
						class="size-5 rounded-full ring-1 ring-white dark:ring-gray-900 bg-white dark:bg-gray-900 object-contain"
						loading="lazy"
					/>
				{/each}
				{#if previewOverflow > 0}
					<div
						class="size-5 rounded-full ring-1 ring-white dark:ring-gray-900 bg-gray-200 dark:bg-gray-800 text-[10px] leading-none font-medium text-gray-700 dark:text-gray-300 flex items-center justify-center"
					>
						+{previewOverflow}
					</div>
				{/if}
			</div>
		{/if}
		<slot />
		{#if state}
			<ChevronUp strokeWidth="2.5" className="size-3.5 " />
		{:else}
			<ChevronDown strokeWidth="2.5" className="size-3.5 " />
		{/if}
	</div>

	<div
		class="text-sm border border-gray-50 dark:border-gray-850/30 rounded-xl my-1.5 p-2 w-full"
		slot="content"
	>
		{#if status?.query}
			<a
				href="https://www.google.com/search?q={status.query}"
				target="_blank"
				class="flex w-full items-center p-1 px-3 group/item justify-between text-gray-800 dark:text-gray-300 font-normal! no-underline!"
			>
				<div class="flex gap-2 items-center">
					<Search />

					<div class=" line-clamp-1">
						{status.query}
					</div>
				</div>

				<div
					class=" ml-1 text-white dark:text-gray-900 group-hover/item:text-gray-600 dark:group-hover/item:text-white transition"
				>
					<!--  -->
					<svg
						xmlns="http://www.w3.org/2000/svg"
						viewBox="0 0 16 16"
						fill="currentColor"
						class="size-4"
					>
						<path
							fill-rule="evenodd"
							d="M4.22 11.78a.75.75 0 0 1 0-1.06L9.44 5.5H5.75a.75.75 0 0 1 0-1.5h5.5a.75.75 0 0 1 .75.75v5.5a.75.75 0 0 1-1.5 0V6.56l-5.22 5.22a.75.75 0 0 1-1.06 0Z"
							clip-rule="evenodd"
						/>
					</svg>
				</div>
			</a>
		{/if}

		{#if status?.items}
			{#each status.items as item, itemIdx}
				<a
					href={item.link}
					target="_blank"
					class="flex w-full items-center p-1 px-3 group/item justify-between text-gray-800 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-850 rounded-lg font-normal! no-underline! mb-1"
				>
					<div class=" flex justify-center items-center gap-3">
						<div class="w-fit">
							<img
								src="https://www.google.com/s2/favicons?sz=32&domain={item.link}"
								alt="{item?.title ?? item.link} favicon"
								class="size-3.5"
							/>
						</div>

						<div class="w-full text-sm line-clamp-1">
							{item?.title ?? item.link}
						</div>
					</div>

					<div
						class=" ml-1 text-white dark:text-gray-900 group-hover/item:text-gray-600 dark:group-hover/item:text-white transition"
					>
						<!--  -->
						<svg
							xmlns="http://www.w3.org/2000/svg"
							viewBox="0 0 16 16"
							fill="currentColor"
							class="size-4"
						>
							<path
								fill-rule="evenodd"
								d="M4.22 11.78a.75.75 0 0 1 0-1.06L9.44 5.5H5.75a.75.75 0 0 1 0-1.5h5.5a.75.75 0 0 1 .75.75v5.5a.75.75 0 0 1-1.5 0V6.56l-5.22 5.22a.75.75 0 0 1-1.06 0Z"
								clip-rule="evenodd"
							/>
						</svg>
					</div>
				</a>
			{/each}
		{:else if status?.urls}
			{#each status.urls as url, urlIdx}
				<a
					href={url}
					target="_blank"
					class="flex w-full items-center p-1 px-3 group/item justify-between text-gray-800 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-850 rounded-lg no-underline mb-1"
				>
					<div class=" flex justify-center items-center gap-3">
						<div class="w-fit">
							<img
								src="https://www.google.com/s2/favicons?sz=32&domain={url}"
								alt="{url} favicon"
								class="size-3.5"
							/>
						</div>

						<div class="w-full text-sm line-clamp-1">
							{url}
						</div>
					</div>

					<div
						class=" ml-1 text-white dark:text-gray-900 group-hover/item:text-gray-600 dark:group-hover/item:text-white transition"
					>
						<!--  -->
						<svg
							xmlns="http://www.w3.org/2000/svg"
							viewBox="0 0 16 16"
							fill="currentColor"
							class="size-4"
						>
							<path
								fill-rule="evenodd"
								d="M4.22 11.78a.75.75 0 0 1 0-1.06L9.44 5.5H5.75a.75.75 0 0 1 0-1.5h5.5a.75.75 0 0 1 .75.75v5.5a.75.75 0 0 1-1.5 0V6.56l-5.22 5.22a.75.75 0 0 1-1.06 0Z"
								clip-rule="evenodd"
							/>
						</svg>
					</div>
				</a>
			{/each}
		{/if}
	</div>
</Collapsible>
