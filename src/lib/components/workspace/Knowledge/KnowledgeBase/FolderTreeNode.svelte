<script lang="ts">
	import dayjs from '$lib/dayjs';
	import duration from 'dayjs/plugin/duration';
	import relativeTime from 'dayjs/plugin/relativeTime';

	dayjs.extend(duration);
	dayjs.extend(relativeTime);

	import { getContext } from 'svelte';
	import { slide } from 'svelte/transition';
	import { quintOut } from 'svelte/easing';

	const i18n = getContext('i18n');

	import { formatFileSize } from '$lib/utils';

	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import DocumentPage from '$lib/components/icons/DocumentPage.svelte';
	import Folder from '$lib/components/icons/Folder.svelte';
	import ChevronDown from '$lib/components/icons/ChevronDown.svelte';
	import ChevronRight from '$lib/components/icons/ChevronRight.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';

	export let node: {
		name: string;
		path: string;
		children: any[];
		files: any[];
	};
	export let expandedKey: string;
	export let expandedSources: Record<string, boolean>;
	export let onClick: (fileId: string) => void;

	// Count all files recursively in a node
	function countFiles(n: any): number {
		let count = n.files.length;
		for (const child of n.children) {
			count += countFiles(child);
		}
		return count;
	}

	$: folderKey = `${expandedKey}/${node.path}`;
	$: totalFiles = countFiles(node);

	const toggle = () => {
		expandedSources[folderKey] = !expandedSources[folderKey];
	};
</script>

<div class="w-full">
	<!-- Subfolder header -->
	<div
		class="group flex items-center w-full px-1.5 py-0.5 hover:bg-gray-50 dark:hover:bg-gray-850/50 rounded-xl transition"
	>
		<button
			class="flex items-center gap-1.5 flex-1 p-1.5 text-left text-sm text-gray-500"
			type="button"
			on:click={toggle}
		>
			<div class="shrink-0">
				{#if expandedSources[folderKey]}
					<ChevronDown className="size-3" strokeWidth="2.5" />
				{:else}
					<ChevronRight className="size-3" strokeWidth="2.5" />
				{/if}
			</div>
			<div class="shrink-0">
				<Folder className="size-3" strokeWidth="2" />
			</div>
			<span class="line-clamp-1 text-xs font-medium">{node.name}</span>
			<span class="text-xs text-gray-400 shrink-0">
				&middot; {$i18n.t('{{count}} files in folder', { count: totalFiles })}
			</span>
		</button>
	</div>

	<!-- Subfolder contents (collapsible) -->
	{#if expandedSources[folderKey]}
		<div transition:slide={{ duration: 300, easing: quintOut, axis: 'y' }}>
			<div class="ml-3 pl-1 border-s border-gray-100 dark:border-gray-900">
				<!-- Child subfolders -->
				{#each node.children as child (child.path)}
					<svelte:self
						node={child}
						{expandedKey}
						bind:expandedSources
						{onClick}
					/>
				{/each}

				<!-- Files in this subfolder -->
				{#each node.files as file (file?.id ?? file?.itemId)}
					<div
						class="flex cursor-pointer w-full px-1.5 py-0.5 hover:bg-gray-50 dark:hover:bg-gray-850/50 rounded-xl transition"
					>
						<button
							class="relative flex items-center gap-1 rounded-xl p-1.5 text-left flex-1 justify-between text-gray-500"
							type="button"
							on:click={() => onClick(file?.id ?? file?.tempId)}
						>
							<div>
								<div class="flex gap-2 items-center line-clamp-1">
									<div class="shrink-0">
										{#if file?.status !== 'uploading'}
											<DocumentPage className="size-3" />
										{:else}
											<Spinner className="size-3" />
										{/if}
									</div>
									<div class="line-clamp-1 text-xs">
										{file?.name ?? file?.meta?.name}
										{#if file?.meta?.size}
											<span class="text-gray-400"
												>{formatFileSize(file?.meta?.size)}</span
											>
										{/if}
									</div>
								</div>
							</div>

							<div class="flex items-center gap-2 shrink-0 text-xs">
								{#if file?.added_at || file?.updated_at}
									<Tooltip
										content={dayjs(
											(file.added_at ?? file.updated_at) * 1000
										).format('LLLL')}
									>
										<div>
											{dayjs(
												(file.added_at ?? file.updated_at) * 1000
											).fromNow()}
										</div>
									</Tooltip>
								{/if}
							</div>
						</button>
					</div>
				{/each}
			</div>
		</div>
	{/if}
</div>
