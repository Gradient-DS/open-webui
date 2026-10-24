<script lang="ts">
	import { WEBUI_BASE_URL } from '$lib/constants';
	import { externalHost, isSafeImageUrl, safeImageUrl } from '$lib/utils/safeImageUrl';

	import { settings } from '$lib/stores';
	import ImagePreview from './ImagePreview.svelte';
	import XMark from '$lib/components/icons/XMark.svelte';
	import Photo from '$lib/components/icons/Photo.svelte';
	import { getContext } from 'svelte';

	export let src = '';
	export let alt = '';
	export let allowExternal = false;

	export let className = ` w-full ${($settings?.highContrastMode ?? false) ? '' : 'outline-hidden focus:outline-hidden'}`;

	export let imageClassName = 'rounded-lg';

	export let dismissible = false;
	export let onDismiss = () => {};

	/** Called when the image fails to load, e.g. its file has since been deleted. */
	export let onError: () => void = () => {};

	const i18n = getContext('i18n');

	let _src = '';
	$: resolvedSrc = src.startsWith('/') ? `${WEBUI_BASE_URL}${src}` : src;
	// [Gradient] An external image loads only once the user clicks it: fetching it would hand whatever its URL
	// carries (a prompt-injected answer can put chat content there) to the image's host.
	let revealed = '';
	$: host = allowExternal ? externalHost(resolvedSrc) : null;
	$: held = host !== null && revealed !== resolvedSrc;
	$: _src = safeImageUrl(resolvedSrc, allowExternal && !held);
	// [Gradient] A rejected URL (e.g. a parsed document's relative figure path)
	// would render the fallback logo as if it were the picture; show the
	// unavailable chip instead.
	$: rejected = !held && !isSafeImageUrl(resolvedSrc, allowExternal);

	let showImagePreview = false;

	let failed = false;
	let attemptedSrc = '';
	$: compactUnavailable = /(?:^|\s)(?:size-|w-|h-)/.test(imageClassName);
	$: if (_src !== attemptedSrc) {
		attemptedSrc = _src;
		failed = false;
	}

	const handleError = () => {
		failed = true;
		showImagePreview = false;
		onError();
	};
</script>

{#if !failed && !rejected && !held}
	<ImagePreview bind:show={showImagePreview} src={_src} {alt} />
{/if}

<div class=" relative group w-fit flex items-center">
	{#if held}
		<button
			type="button"
			class="{imageClassName} inline-flex h-[1.6875rem] min-w-8 max-w-full items-center gap-1.5 overflow-hidden border border-gray-100/50 bg-gray-50/40 px-2 text-gray-500 hover:text-gray-700 dark:border-white/[0.04] dark:bg-white/[0.03] dark:text-gray-400 dark:hover:text-gray-200"
			data-cy="image-held"
			title={resolvedSrc}
			on:click={() => (revealed = resolvedSrc)}
		>
			<Photo className="size-3.5 shrink-0" strokeWidth="1.5" />
			<span class="truncate text-xs">{$i18n.t('Load image from {{host}}', { host })}</span>
		</button>
	{:else if failed || rejected}
		<div
			class="{imageClassName} inline-flex {compactUnavailable
				? ''
				: 'h-[1.6875rem] min-w-8 gap-1.5 px-2'} items-center justify-center overflow-hidden border border-gray-100/50 bg-gray-50/40 text-gray-500 dark:border-white/[0.04] dark:bg-white/[0.03] dark:text-gray-400"
			data-cy="image-unavailable"
		>
			<Photo className="size-3.5 shrink-0" strokeWidth="1.5" />
			<span class:hidden={compactUnavailable} class="truncate text-xs">
				{$i18n.t('Image unavailable')}
			</span>
		</div>
	{:else}
		<button
			class={className}
			on:click={() => {
				showImagePreview = true;
			}}
			aria-label={$i18n.t('Show image preview')}
			type="button"
		>
			<img
				src={_src}
				{alt}
				class={imageClassName}
				draggable="false"
				data-cy="image"
				on:error={handleError}
			/>
		</button>
	{/if}

	{#if dismissible}
		<div class=" absolute -top-1 -right-1">
			<button
				aria-label={$i18n.t('Remove image')}
				class=" bg-white text-black border border-white rounded-full hover-reveal transition"
				type="button"
				on:click={() => {
					onDismiss();
				}}
			>
				<XMark className={'size-4'} />
			</button>
		</div>
	{/if}
</div>
