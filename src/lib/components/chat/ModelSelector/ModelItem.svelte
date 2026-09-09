<script lang="ts">
	import { WEBUI_API_BASE_URL } from '$lib/constants';
	import { marked } from 'marked';

	import { getContext, tick } from 'svelte';
	import dayjs from '$lib/dayjs';

	import { config, mobile, settings, user } from '$lib/stores';

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
		parseHosting,
		hostingFromHost,
		hostingFromDeployment,
		infoTooltipHtml
	} from '$lib/utils/models/profile';

	const i18n = getContext('i18n');

	export let selectedModelIdx: number = -1;
	export let item: any = {};
	export let index: number = -1;
	export let value: string | null = '';
	export let selectedValues: string[] = [];
	export let compareEnabled = false;

	// [Gradient] Two-line model profiles, hosting warnings and meters.
	$: profile = resolveModelProfile(
		item?.model ?? {},
		$config?.model_profiles ?? [],
		$i18n.language
	);
	$: displayName = item?.label || item?.value || '';
	$: infoTooltip = infoTooltipHtml(profile.info);
	// Hosting/datacenter precedence (first non-empty wins):
	//   1. per-deployment MODEL_HOSTING rules ($config.model_hosting) — authoritative,
	//      because the same model id can live in a different datacenter per client;
	//   2. the live upstream connection host (only resolves when connected direct, not
	//      via the shared gateway where every model reports the same host);
	//   3. the baked profile `hosting` (usually empty now that hosting is deployment-set).
	$: hosting = parseHosting(
		hostingFromDeployment($config?.model_hosting, item?.model?.id ?? '', item?.model?.name ?? '') ??
			hostingFromHost(item?.model?.connection_host) ??
			profile.hosting
	);
	// Whether to show the baked Quality/Speed meters. Disabled per-deployment via
	// FEATURE_MODEL_METERS when overlapping model names make the baked ratings
	// unreliable; descriptions, eco badge and the datacenter flag are unaffected.
	$: showMeters = $config?.features?.feature_model_meters !== false;
	// Warn when the model is hosted outside NL/EU (data sovereignty). Inferred from the
	// hosting country flag; unknown hosting (no flag) shows no warning.
	$: dataWarning = !!hosting?.flag && hosting.flag !== 'NL' && hosting.flag !== 'EU';
	// Admin-set model description (custom/workspace models). Folded into the single (i)
	// tooltip below so a row never shows two separate info icons.
	$: description = item?.model?.info?.meta?.description ?? '';
	$: descriptionHtml = description
		? marked.parse(sanitizeResponseContent(description).replaceAll('\n', '<br>'))
		: '';

	export let unloadModelHandler: (modelValue: string) => void = () => {};
	export let pinModelHandler: (modelId: string) => void = () => {};
	export let deleteModelHandler: (model: any) => void = () => {};
	export let selectionOnly = false;

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

	const formatSize = (size?: number) => (size ? `(${(size / 1024 ** 3).toFixed(1)}GB)` : '');

	let showMenu = false;
	$: isSelected = compareEnabled ? selectedValues.includes(item.value) : value === item.value;
</script>

<button
	role="option"
	aria-selected={isSelected}
	aria-label={$i18n.t('Select {{modelName}} model', { modelName: item.label })}
	class="focus-ring group/item flex h-14 w-full cursor-pointer select-none items-center rounded-xl px-2 text-left text-[0.8125rem] font-normal text-gray-700 outline-hidden transition-colors duration-75 dark:text-gray-100 {($settings?.highContrastMode ??
	false)
		? 'hover:bg-gray-200 dark:hover:bg-gray-800'
		: 'hover:bg-gray-50/40 dark:hover:bg-gray-800/40'} {index === selectedModelIdx &&
	!compareEnabled
		? ($settings?.highContrastMode ?? false)
			? 'bg-gray-200 dark:bg-gray-800'
			: 'bg-gray-50/70 dark:bg-gray-800/60'
		: ''} {isSelected
		? ($settings?.highContrastMode ?? false)
			? 'bg-gray-200 dark:bg-gray-800'
			: 'bg-gray-50/70 dark:bg-gray-800/60'
		: ''}"
	data-arrow-selected={index === selectedModelIdx}
	data-value={item.value}
	on:click={() => {
		onClick();
	}}
>
	<div class="flex flex-col flex-1 gap-0.5 min-w-0">
		<div class="flex items-center gap-2 min-w-0">
			<div class="flex items-center min-w-fit">
				<Tooltip content={$user?.role === 'admin' ? (item?.value ?? '') : ''} placement="top-start">
					<img
						src={`${WEBUI_API_BASE_URL}/models/model/profile/image?id=${item.model.id}&lang=${$i18n.language}`}
						alt={$i18n.t('{{modelName}} profile image', { modelName: item.label })}
						class="flex size-4 items-center rounded-full"
						loading="lazy"
						on:error={(e) => {
							// LICENSE covers this Open WebUI fallback logo.
							// Do not alter, remove, obscure, or replace it except as LICENSE permits:
							// https://docs.openwebui.com/license.
							e.currentTarget.src = '/favicon.png';
						}}
					/>
				</Tooltip>
			</div>

			<div class="flex min-w-0 items-center">
				<Tooltip content={`${item.label} (${item.value})`} placement="top-start">
					<div class="line-clamp-1 font-medium">
						{profile.bestFor || displayName}
					</div>
				</Tooltip>
			</div>

			<div class=" shrink-0 flex items-center gap-2">
				{#if profile.info || description || hosting}
					{#key item.model.id}
						<Tooltip elementId="model-info-{item.model.id}">
							<InfoCircle className="size-3.5 text-gray-400 dark:text-gray-500" />

							<div slot="tooltip" id="model-info-{item.model.id}" class="text-left">
								{#if profile.info}
									<!-- eslint-disable-next-line svelte/no-at-html-tags — infoTooltip is escaped in infoTooltipHtml; the only markup is the <br> it adds -->
									<div>{@html infoTooltip}</div>
								{/if}
								{#if description}
									<div class={profile.info ? 'mt-1.5 pt-1.5 border-t border-white/15' : ''}>
										<!-- eslint-disable-next-line svelte/no-at-html-tags — descriptionHtml is escaped by sanitizeResponseContent before marked.parse -->
										{@html descriptionHtml}
									</div>
								{/if}
								{#if hosting}
									<div
										class="flex items-center gap-1.5 {profile.info || description
											? 'mt-1.5 pt-1.5 border-t border-white/15'
											: ''}"
									>
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
								<span
									class="line-clamp-1 text-[0.6875rem] font-normal text-gray-500 dark:text-gray-400"
									>{item.model.ollama?.details?.parameter_size ?? ''}</span
								>
							</Tooltip>
						</div>
					{/if}
				{:else if item.model.provider === 'lmstudio' || item.model.provider === 'llama.cpp'}
					{@const parameterSize =
						item.model.params_string ?? item.model.details?.parameter_size ?? ''}
					{@const quantization =
						item.model.quantization?.name ?? item.model.details?.quantization_level ?? ''}
					{@const size = item.model.size_bytes ?? item.model.size}
					{#if parameterSize || quantization || size}
						<div class="flex items-center translate-y-[0.5px]">
							<Tooltip
								content={`${quantization ? `${quantization} ` : ''}${formatSize(size)}`}
								className="self-end"
							>
								<span
									class="line-clamp-1 text-[0.6875rem] font-normal text-gray-500 dark:text-gray-400"
								>
									{parameterSize || quantization || formatSize(size)}
								</span>
							</Tooltip>
						</div>
					{/if}
				{/if}

				{#if item.model.loaded}
					<div class="flex items-center px-0.5">
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
								<span class="relative flex size-1.5">
									<span
										class="animate-ping absolute inline-flex h-full w-full rounded-full bg-green-400 opacity-75"
									/>
									<span class="relative inline-flex size-1.5 rounded-full bg-green-500" />
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
										<div class=" text-xs font-normal rounded-sm uppercase text-white">
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
			</div>
		</div>

		{#if profile.bestFor && displayName && displayName !== profile.bestFor}
			<div class="text-[11px] sm:text-xs text-gray-500 dark:text-gray-400 line-clamp-1">
				{displayName}
			</div>
		{/if}
	</div>

	<div class="ml-auto pl-2 pr-1 flex items-center gap-2 shrink-0">
		<div class="w-9 shrink-0 flex items-center justify-end gap-1.5">
			{#if dataWarning}
				<Tooltip
					content={$i18n.t(
						'This model is not hosted on Dutch private cloud. Be careful when sharing sensitive data.'
					)}
				>
					<ExclamationTriangle className="size-3.5 text-amber-500 dark:text-amber-400" />
				</Tooltip>
			{/if}

			{#if profile.eco}
				<Tooltip content={$i18n.t('Energy efficient')}>
					<Leaf className="size-3.5 text-green-600 dark:text-green-500" strokeWidth="1.75" />
				</Tooltip>
			{/if}
		</div>

		{#if showMeters}
			<ModelProfile {profile} />
		{/if}
		<div class="flex items-center justify-end gap-1.5 w-8 shrink-0">
			{#if !selectionOnly && $user?.role === 'admin' && item.model.loaded}
				<Tooltip
					content={`${$i18n.t('Eject')}`}
					className="flex-shrink-0 group-hover/item:opacity-100 opacity-0 "
				>
					<button
						class="focus-ring flex"
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

			{#if !selectionOnly}
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
						class="focus-ring flex"
						on:click={(e) => {
							e.preventDefault();
							e.stopPropagation();
							showMenu = !showMenu;
						}}
					>
						<EllipsisHorizontal />
					</button>
				</ModelItemMenu>
			{/if}

			<!-- Always reserve the checkmark slot so the selected row's meters stay aligned with the rest -->
			<div class="size-3 flex items-center justify-center shrink-0">
				{#if isSelected}
					<Check className="size-3" />
				{/if}
			</div>
		</div>
	</div>
</button>
