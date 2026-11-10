<script lang="ts">
	import { getContext } from 'svelte';
	import { goto } from '$app/navigation';

	import Dropdown from '$lib/components/common/Dropdown.svelte';
	import DropdownMenu from '$lib/components/common/DropdownMenu.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import Pin from '$lib/components/icons/Pin.svelte';
	import PinSlash from '$lib/components/icons/PinSlash.svelte';
	import Link from '$lib/components/icons/Link.svelte';
	import Pencil from '$lib/components/icons/Pencil.svelte';
	import { config, pinnedModels, settings, user } from '$lib/stores';
	import GlobeAlt from '$lib/components/icons/GlobeAlt.svelte';

	const i18n = getContext('i18n');

	export let show = false;
	export let model;

	export let pinModelHandler: (modelId: string) => void = () => {};
	export let copyLinkHandler: Function = () => {};
	export let deleteModelHandler: Function = () => {};

	export let onClose: Function = () => {};

	const providerSupportsDelete = (provider = '') => provider === 'llama.cpp';
</script>

<Dropdown
	bind:show
	align="end"
	sideOffset={-2}
	onOpenChange={(state) => {
		if (state === false) {
			onClose();
		}
	}}
>
	<Tooltip
		content={$i18n.t('More')}
		className={($settings?.highContrastMode ?? false)
			? ''
			: 'group-hover/item:opacity-100 opacity-0'}
	>
		<slot />
	</Tooltip>

	<div slot="content">
		<DropdownMenu className="min-w-[13.125rem] z-[9999999]">
			{#if model?.preset || model?.info?.base_model_id ? model?.info?.user_id === $user?.id : false}
				<button
					type="button"
					class="select-none flex h-[1.6875rem] w-full items-center gap-2 rounded-xl px-2 text-[0.8125rem] hover:bg-gray-50/40 dark:hover:bg-gray-800/40 transition"
					on:click={(e) => {
						e.stopPropagation();
						e.preventDefault();

						goto(`/workspace/models/edit?id=${encodeURIComponent(model?.id ?? '')}`);
						show = false;
					}}
				>
					<Pencil className="size-3.5" />

					<div class="flex items-center">{$i18n.t('Edit')}</div>
				</button>

				<hr class="border-gray-50 dark:border-gray-800/30 mx-1 my-0.5" />
			{/if}

			<!-- [Gradient] Pin assistants, and allow unpinning admin-default base models. -->
			{#if model?.info?.base_model_id || $pinnedModels.includes(model?.id)}
				<button
					type="button"
					aria-pressed={$pinnedModels.includes(model?.id)}
					class="select-none flex h-[1.6875rem] w-full items-center gap-2 rounded-xl px-2 text-[0.8125rem] hover:bg-gray-50/40 dark:hover:bg-gray-800/40 transition"
					on:click={(e) => {
						e.stopPropagation();
						e.preventDefault();

						pinModelHandler(model?.id);
						show = false;
					}}
				>
					{#if $pinnedModels.includes(model?.id)}
						<PinSlash className="size-3.5" />
					{:else}
						<Pin className="size-3.5" />
					{/if}

					<div class="flex items-center">
						{#if $pinnedModels.includes(model?.id)}
							{$i18n.t('Hide from Sidebar')}
						{:else}
							{$i18n.t('Keep in Sidebar')}
						{/if}
					</div>
				</button>
			{/if}

			<button
				type="button"
				class="select-none flex h-[1.6875rem] w-full items-center gap-2 rounded-xl px-2 text-[0.8125rem] hover:bg-gray-50/40 dark:hover:bg-gray-800/40 transition"
				on:click={(e) => {
					e.stopPropagation();
					e.preventDefault();

					copyLinkHandler();
					show = false;
				}}
			>
				<Link className="size-3.5" />

				<div class="flex items-center">{$i18n.t('Copy Link')}</div>
			</button>

			{#if $config?.features.enable_community_sharing}
				<hr class="border-gray-50 dark:border-gray-800/30 mx-1 my-0.5" />

				<button
					type="button"
					class="select-none flex h-[1.6875rem] w-full items-center gap-2 rounded-xl px-2 text-[0.8125rem] hover:bg-gray-50/40 dark:hover:bg-gray-800/40 transition"
					on:click={(e) => {
						e.stopPropagation();
						e.preventDefault();

						window.open(
							`https://openwebui.com/models?q=${encodeURIComponent(model?.id ?? '')}`,
							'_blank'
						);
						show = false;
					}}
				>
					<GlobeAlt className="size-3.5" />

					<div class="flex items-center">{$i18n.t('Community Reviews')}</div>
				</button>
			{/if}
		</DropdownMenu>
	</div>
</Dropdown>
