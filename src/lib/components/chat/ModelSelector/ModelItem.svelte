<script lang="ts">
	import { marked } from 'marked';

	import { getContext, tick } from 'svelte';
	import dayjs from '$lib/dayjs';

	import { mobile, settings, user } from '$lib/stores';

	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import { copyToClipboard, sanitizeResponseContent } from '$lib/utils';
	import ArrowUpTray from '$lib/components/icons/ArrowUpTray.svelte';
	import Check from '$lib/components/icons/Check.svelte';
	import ModelItemMenu from './ModelItemMenu.svelte';
	import ModelProfile from './ModelProfile.svelte';
	import EllipsisHorizontal from '$lib/components/icons/EllipsisHorizontal.svelte';
	import { toast } from 'svelte-sonner';
	import Tag from '$lib/components/icons/Tag.svelte';
	import Label from '$lib/components/icons/Label.svelte';
	import Leaf from '$lib/components/icons/Leaf.svelte';
	import InfoCircle from '$lib/components/icons/InfoCircle.svelte';
	import ExclamationTriangle from '$lib/components/icons/ExclamationTriangle.svelte';
	import Flag from '$lib/components/icons/Flag.svelte';
	import {
		resolveModelProfile,
		ORIGIN_META,
		parseHosting,
		hostingFromHost
	} from '$lib/utils/models/profile';

	const i18n = getContext('i18n');

	export let selectedModelIdx: number = -1;
	export let item: any = {};
	export let index: number = -1;
	export let value: string = '';

	$: profile = resolveModelProfile(item?.model ?? {});
	$: displayName = item?.label || item?.value || '';
	$: infoTooltip = (profile.info ?? '').replaceAll('\n', '<br>');
	$: originMeta = profile.origin ? ORIGIN_META[profile.origin] : undefined;
	// Prefer the live hosting derived from the upstream connection host (LiteLLM/OpenAI
	// api_base), falling back to the static hosting value from the model profile.
	$: hosting = parseHosting(hostingFromHost(item?.model?.connection_host) ?? profile.hosting);

	export let unloadModelHandler: (modelValue: string) => void = () => {};
	export let pinModelHandler: (modelId: string) => void = () => {};
	export let deleteModelHandler: (model: any) => void = () => {};

	export let onClick: () => void = () => {};

	const copyLinkHandler = async (model) => {
		const baseUrl = window.location.origin;
		const res = await copyToClipboard(`${baseUrl}/?model=${encodeURIComponent(model.id)}`);

		if (res) {
			toast.success($i18n.t('Copied link to clipboard'));
		} else {
			toast.error($i18n.t('Failed to copy link'));
		}
	};

	let showMenu = false;
</script>

<button
	role="option"
	aria-selected={value === item.value}
	aria-label={$i18n.t('Select {{modelName}} model', { modelName: item.label })}
	class="flex group/item w-full h-14 text-left font-medium select-none items-center rounded-button pl-3 pr-1.5 text-sm text-gray-700 dark:text-gray-100 outline-hidden transition-all duration-75 hover:bg-gray-100 dark:hover:bg-gray-800 rounded-xl cursor-pointer data-highlighted:bg-muted {index ===
	selectedModelIdx
		? 'bg-gray-100 dark:bg-gray-800 group-hover:bg-transparent'
		: ''}"
	data-arrow-selected={index === selectedModelIdx}
	data-value={item.value}
	on:click={() => {
		onClick();
	}}
>
	<div class="flex flex-col flex-1 gap-0.5 min-w-0">
		<div class="flex items-center gap-2 min-w-0">
			<div class="flex items-center min-w-0">
				<Tooltip content={`${item.label} (${item.value})`} placement="top-start">
					<div class="line-clamp-1 font-medium">
						{profile.bestFor || displayName}
					</div>
				</Tooltip>
			</div>

			<div class=" shrink-0 flex items-center gap-2">
				{#if profile.info || originMeta || hosting}
					{#key item.model.id}
						<Tooltip elementId="model-info-{item.model.id}">
							<InfoCircle className="size-3.5 text-gray-400 dark:text-gray-500" />

							<div slot="tooltip" id="model-info-{item.model.id}" class="text-left">
								{#if profile.info}
									<div>{@html infoTooltip}</div>
								{/if}
								{#if originMeta || hosting}
									<div
										class="flex flex-col gap-1 {profile.info
											? 'mt-1.5 pt-1.5 border-t border-white/15'
											: ''}"
									>
										{#if originMeta}
											<div class="flex items-center gap-1.5">
												<span>{$i18n.t('Developed in')}: {$i18n.t(originMeta.labelKey)}</span>
												<Flag
													origin={profile.origin}
													className="w-[18px] h-[13px] rounded-[2px]"
													ariaLabel={$i18n.t(originMeta.labelKey)}
												/>
											</div>
										{/if}
										{#if hosting}
											<div class="flex items-center gap-1.5">
												<span>{$i18n.t('Hosting location')}: {hosting.label}</span>
												{#if hosting.flag}
													<Flag
														origin={hosting.flag}
														className="w-[18px] h-[13px] rounded-[2px]"
														ariaLabel={hosting.flag}
													/>
												{/if}
											</div>
										{/if}
									</div>
								{/if}
							</div>
						</Tooltip>
					{/key}
				{/if}

				{#if item.model.owned_by === 'ollama'}
					{#if (item.model.ollama?.details?.parameter_size ?? '') !== ''}
						<div class="flex items-center translate-y-[0.5px]">
							<Tooltip
								content={`${
									item.model.ollama?.details?.quantization_level
										? item.model.ollama?.details?.quantization_level + ' '
										: ''
								}${
									item.model.ollama?.size
										? `(${(item.model.ollama?.size / 1024 ** 3).toFixed(1)}GB)`
										: ''
								}`}
								className="self-end"
							>
								<span class=" text-xs font-medium text-gray-600 dark:text-gray-400 line-clamp-1"
									>{item.model.ollama?.details?.parameter_size ?? ''}</span
								>
							</Tooltip>
						</div>
					{/if}
				{/if}

				{#if item.model.loaded}
					<div class="flex items-center translate-y-[0.5px] px-0.5">
						<Tooltip
							content={item.model.ollama?.expires_at &&
							new Date(item.model.ollama?.expires_at * 1000) > new Date()
								? `${$i18n.t('Unloads {{FROM_NOW}}', {
										FROM_NOW: dayjs(item.model.ollama?.expires_at * 1000).fromNow()
									})}`
								: `${$i18n.t('Loaded')}`}
							className="self-end"
						>
							<div class=" flex items-center">
								<span class="relative flex size-2">
									<span
										class="animate-ping absolute inline-flex h-full w-full rounded-full bg-green-400 opacity-75"
									/>
									<span class="relative inline-flex rounded-full size-2 bg-green-500" />
								</span>
							</div>
						</Tooltip>
					</div>
				{/if}

				<!-- {JSON.stringify(item.info)} -->

				{#if (item?.model?.tags ?? []).length > 0}
					{#key item.model.id}
						<Tooltip elementId="tags-{item.model.id}">
							<div slot="tooltip" id="tags-{item.model.id}">
								{#each item.model?.tags.sort((a, b) => a.name.localeCompare(b.name)) as tag}
									<Tooltip content={tag.name} className="flex-shrink-0">
										<div class=" text-xs font-medium rounded-sm uppercase text-white">
											{tag.name}
										</div>
									</Tooltip>
								{/each}
							</div>

							<div class="translate-y-[1px]">
								<Tag />
							</div>
						</Tooltip>
					{/key}
				{/if}

				{#if item.model?.direct}
					<Tooltip content={`${$i18n.t('Direct')}`}>
						<div class="translate-y-[1px]">
							<svg
								xmlns="http://www.w3.org/2000/svg"
								viewBox="0 0 16 16"
								fill="currentColor"
								class="size-3"
							>
								<path
									fill-rule="evenodd"
									d="M2 2.75A.75.75 0 0 1 2.75 2C8.963 2 14 7.037 14 13.25a.75.75 0 0 1-1.5 0c0-5.385-4.365-9.75-9.75-9.75A.75.75 0 0 1 2 2.75Zm0 4.5a.75.75 0 0 1 .75-.75 6.75 6.75 0 0 1 6.75 6.75.75.75 0 0 1-1.5 0C8 10.35 5.65 8 2.75 8A.75.75 0 0 1 2 7.25ZM3.5 11a1.5 1.5 0 1 0 0 3 1.5 1.5 0 0 0 0-3Z"
									clip-rule="evenodd"
								/>
							</svg>
						</div>
					</Tooltip>
				{/if}

				{#if item.model?.info?.meta?.description}
					<Tooltip
						content={`${marked.parse(
							sanitizeResponseContent(item.model?.info?.meta?.description).replaceAll('\n', '<br>')
						)}`}
					>
						<div class=" translate-y-[1px]">
							<svg
								xmlns="http://www.w3.org/2000/svg"
								fill="none"
								viewBox="0 0 24 24"
								stroke-width="1.5"
								stroke="currentColor"
								class="w-4 h-4"
							>
								<path
									stroke-linecap="round"
									stroke-linejoin="round"
									d="m11.25 11.25.041-.02a.75.75 0 0 1 1.063.852l-.708 2.836a.75.75 0 0 0 1.063.853l.041-.021M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Zm-9-3.75h.008v.008H12V8.25Z"
								/>
							</svg>
						</div>
					</Tooltip>
				{/if}
			</div>
		</div>

		{#if displayName && displayName !== profile.bestFor}
			<div class="text-xs text-gray-500 dark:text-gray-400 line-clamp-1">
				{displayName}
			</div>
		{/if}
	</div>

	<div class="ml-auto pl-2 pr-1 flex items-center gap-2 shrink-0">
		<div class="w-9 shrink-0 flex items-center justify-end gap-1.5">
			{#if profile.local === false}
				<Tooltip
					content={$i18n.t(
						'This model does not run on our own servers. Be careful when sharing sensitive data.'
					)}
				>
					<ExclamationTriangle
						className="size-3.5 text-amber-500 dark:text-amber-400"
						strokeWidth="2"
					/>
				</Tooltip>
			{/if}

			{#if profile.eco}
				<Tooltip content={$i18n.t('Energy efficient')}>
					<Leaf className="size-3.5 text-green-600 dark:text-green-500" strokeWidth="1.75" />
				</Tooltip>
			{/if}
		</div>

		<ModelProfile {profile} />
		<div class="flex items-center justify-end gap-1.5 w-8 shrink-0">
		{#if $user?.role === 'admin' && item.model.loaded}
			<Tooltip
				content={`${$i18n.t('Eject')}`}
				className="flex-shrink-0 group-hover/item:opacity-100 opacity-0 "
			>
				<button
					class="flex"
					aria-label={$i18n.t('Eject model')}
					on:click={(e) => {
						e.preventDefault();
						e.stopPropagation();
						unloadModelHandler(item.value);
					}}
				>
					<ArrowUpTray className="size-3" />
				</button>
			</Tooltip>
		{/if}

		<ModelItemMenu
			bind:show={showMenu}
			model={item.model}
			{pinModelHandler}
			{deleteModelHandler}
			copyLinkHandler={() => {
				copyLinkHandler(item.model);
			}}
		>
			<button
				aria-label={`${$i18n.t('More Options')}`}
				class="flex"
				on:click={(e) => {
					e.preventDefault();
					e.stopPropagation();
					showMenu = !showMenu;
				}}
			>
				<EllipsisHorizontal />
			</button>
		</ModelItemMenu>

			<!-- Always reserve the checkmark slot so the selected row's meters stay aligned with the rest -->
			<div class="size-3 flex items-center justify-center shrink-0">
				{#if value === item.value}
					<Check className="size-3" />
				{/if}
			</div>
		</div>
	</div>
</button>
