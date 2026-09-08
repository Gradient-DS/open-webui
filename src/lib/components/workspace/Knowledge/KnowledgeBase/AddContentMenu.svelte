<script lang="ts">
	import { getContext, createEventDispatcher } from 'svelte';
	const dispatch = createEventDispatcher();

	import Dropdown from '$lib/components/common/Dropdown.svelte';
	import DropdownMenu from '$lib/components/common/DropdownMenu.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import ArrowUpCircle from '$lib/components/icons/ArrowUpCircle.svelte';
	import BarsArrowUp from '$lib/components/icons/BarsArrowUp.svelte';
	import FolderOpen from '$lib/components/icons/FolderOpen.svelte';
	import NewFolderAlt from '$lib/components/icons/NewFolderAlt.svelte';
	import ArrowPath from '$lib/components/icons/ArrowPath.svelte';
	import GlobeAlt from '$lib/components/icons/GlobeAlt.svelte';
	import ArrowUturnLeft from '$lib/components/icons/ArrowUturnLeft.svelte';

	const i18n = getContext('i18n');

	export let onClose: Function = () => {};

	export let onUpload: Function = (data) => {};
	// Structure-write items (New directory / Sync directory / Reset) only
	// render when the parent passes the affordance — i.e. structureEditable
	// local KBs. Cloud / push KBs never get them.
	export let structureEditable = false;
	export let onSync: Function | null = null;
	export let onReset: Function | null = null;

	let show = false;
</script>

<Dropdown
	bind:show
	onOpenChange={(state) => {
		if (state === false) {
			onClose();
		}
	}}
	align="end"
>
	<Tooltip content={$i18n.t('Add Content')}>
		<button
			class="p-1.5 rounded-xl bg-transparent transition text-xs flex items-center space-x-1 hover:text-gray-900 dark:hover:text-gray-100"
			aria-label={$i18n.t('Add Content')}
			on:click={(e) => {
				e.stopPropagation();
				show = true;
			}}
		>
			<svg
				xmlns="http://www.w3.org/2000/svg"
				viewBox="0 0 16 16"
				fill="currentColor"
				class="w-4 h-4"
			>
				<path
					d="M8.75 3.75a.75.75 0 0 0-1.5 0v3.5h-3.5a.75.75 0 0 0 0 1.5h3.5v3.5a.75.75 0 0 0 1.5 0v-3.5h3.5a.75.75 0 0 0 0-1.5h-3.5v-3.5Z"
				/>
			</svg>
		</button>
	</Tooltip>

	<div slot="content">
<<<<<<< HEAD
		<div
			class="min-w-[200px] rounded-2xl px-1 py-1 border border-gray-100 dark:border-gray-800 z-50 bg-white dark:bg-gray-850 dark:text-white shadow-lg transition"
		>
			{#if structureEditable}
				<button
					class="select-none flex gap-2 items-center px-3 py-1.5 text-sm cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800 rounded-xl w-full"
					on:click={() => {
						onUpload({ type: 'new_directory' });
						show = false;
					}}
				>
					<NewFolderAlt />
					<div class="flex items-center">{$i18n.t('New directory')}</div>
				</button>

				<hr class="my-1 border-gray-100 dark:border-gray-800" />
			{/if}

=======
		<DropdownMenu className="min-w-[12.5rem] transition">
>>>>>>> upstream/main
			<button
				class="select-none flex h-[1.6875rem] w-full cursor-pointer items-center gap-2 rounded-xl bg-transparent px-2 text-xs hover:text-gray-900 dark:hover:text-gray-100"
				on:click={() => {
					show = false;
<<<<<<< HEAD
=======
				}}
			>
				<NewFolderAlt />
				<div class="flex items-center">{$i18n.t('New directory')}</div>
			</button>

			<hr class="my-1 border-gray-100 dark:border-gray-800" />

			<button
				class="select-none flex h-[1.6875rem] w-full cursor-pointer items-center gap-2 rounded-xl bg-transparent px-2 text-xs hover:text-gray-900 dark:hover:text-gray-100"
				on:click={() => {
>>>>>>> upstream/main
					onUpload({ type: 'files' });
				}}
			>
				<ArrowUpCircle strokeWidth="2" />
				<div class="flex items-center">{$i18n.t('Upload files')}</div>
			</button>

			<button
				class="select-none flex h-[1.6875rem] w-full cursor-pointer items-center gap-2 rounded-xl bg-transparent px-2 text-xs hover:text-gray-900 dark:hover:text-gray-100"
				on:click={() => {
					show = false;
					onUpload({ type: 'directory' });
				}}
			>
				<FolderOpen strokeWidth="2" />
				<div class="flex items-center">{$i18n.t('Upload directory')}</div>
			</button>

<<<<<<< HEAD
			{#if onSync}
				<Tooltip
					content={$i18n.t(
						'Sync a local directory with this knowledge base. Only new and modified files will be uploaded. The directory structure will be mirrored.'
					)}
					className="w-full"
=======
			<Tooltip
				content={$i18n.t(
					'Sync a local directory with this knowledge base. Only new and modified files will be uploaded. The directory structure will be mirrored.'
				)}
				className="w-full"
			>
				<button
					class="select-none flex h-[1.6875rem] w-full cursor-pointer items-center gap-2 rounded-xl bg-transparent px-2 text-xs hover:text-gray-900 dark:hover:text-gray-100"
					on:click={() => {
						onSync();
					}}
>>>>>>> upstream/main
				>
					<button
						class="select-none flex gap-2 items-center px-3 py-1.5 text-sm cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800 rounded-xl w-full"
						on:click={() => {
							show = false;
							onSync();
						}}
					>
						<ArrowPath strokeWidth="2" />
						<div class="flex items-center">{$i18n.t('Sync directory')}</div>
					</button>
				</Tooltip>
			{/if}

			<button
				class="select-none flex h-[1.6875rem] w-full cursor-pointer items-center gap-2 rounded-xl bg-transparent px-2 text-xs hover:text-gray-900 dark:hover:text-gray-100"
				on:click={() => {
					show = false;
					onUpload({ type: 'web' });
				}}
			>
				<GlobeAlt strokeWidth="2" />
				<div class="flex items-center">{$i18n.t('Add webpage')}</div>
			</button>

			<button
				class="select-none flex h-[1.6875rem] w-full cursor-pointer items-center gap-2 rounded-xl bg-transparent px-2 text-xs hover:text-gray-900 dark:hover:text-gray-100"
				on:click={() => {
					show = false;
					onUpload({ type: 'text' });
				}}
			>
				<BarsArrowUp strokeWidth="2" />
				<div class="flex items-center">{$i18n.t('Add text content')}</div>
			</button>

			{#if onReset}
				<hr class="my-1 border-gray-100 dark:border-gray-800" />

<<<<<<< HEAD
				<button
					class="select-none flex gap-2 items-center px-3 py-1.5 text-sm cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800 rounded-xl w-full"
					on:click={() => {
						onReset();
						show = false;
					}}
				>
					<ArrowUturnLeft strokeWidth="2" />
					<div class="flex items-center">{$i18n.t('Reset')}</div>
				</button>
			{/if}
		</div>
=======
			<button
				class="select-none flex h-[1.6875rem] w-full cursor-pointer items-center gap-2 rounded-xl bg-transparent px-2 text-xs hover:text-gray-900 dark:hover:text-gray-100"
				on:click={() => {
					onReset();
					show = false;
				}}
			>
				<ArrowUturnLeft strokeWidth="2" />
				<div class="flex items-center">{$i18n.t('Reset')}</div>
			</button>
		</DropdownMenu>
>>>>>>> upstream/main
	</div>
</Dropdown>
