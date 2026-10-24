<script lang="ts">
	// [Gradient] A picture an agent delivered, shown in the chat with copy and download
	// buttons; its card when it cannot be read.
	import type { Writable } from 'svelte/store';
	import type { i18n as i18nType } from 'i18next';
	import { getContext, onMount } from 'svelte';
	import { toast } from 'svelte-sonner';

	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import Check from '$lib/components/icons/Check.svelte';
	import Clipboard from '$lib/components/icons/Clipboard.svelte';
	import Download from '$lib/components/icons/Download.svelte';
	import DocumentCard from './Markdown/DocumentCard.svelte';
	import {
		downloadDeliveredFile,
		loadImage,
		pngBlob,
		type DeliveredFile
	} from '$lib/utils/deliveredFile';

	export let file: DeliveredFile;
	export let chatId: string;

	const i18n = getContext<Writable<i18nType>>('i18n');

	let src = '';
	let failed = false;
	let copied = false;

	onMount(async () => {
		try {
			src = await loadImage(file, chatId);
		} catch {
			failed = true;
		}
	});

	const copy = async () => {
		try {
			// Clipboards take PNG only: a JPEG is converted first.
			await navigator.clipboard.write([new ClipboardItem({ 'image/png': pngBlob(src) })]);
			copied = true;
			setTimeout(() => (copied = false), 1500);
		} catch (e) {
			console.error(e);
			toast.error($i18n.t('Failed to copy image'));
		}
	};

	const download = async () => {
		try {
			await downloadDeliveredFile(file, chatId);
		} catch (e) {
			console.error(e);
			toast.error($i18n.t('Failed to download file'));
		}
	};

	const button =
		'p-1.5 rounded-lg bg-white/90 dark:bg-gray-850/90 text-gray-700 dark:text-gray-200 border border-gray-100 dark:border-gray-800 shadow-xs backdrop-blur-sm hover:bg-gray-50 dark:hover:bg-gray-800 transition';
</script>

{#if failed}
	<DocumentCard title={file.name} {file} {chatId} />
{:else if src}
	<div class="group relative max-w-md">
		<img {src} alt={file.name} class="w-full rounded-lg" />
		<div
			class="absolute top-2 right-2 flex gap-1 opacity-0 group-hover:opacity-100 focus-within:opacity-100 transition"
		>
			<Tooltip content={copied ? $i18n.t('Copied') : $i18n.t('Copy image')}>
				<button class={button} aria-label={$i18n.t('Copy image')} on:click={copy}>
					{#if copied}
						<Check className="size-4" strokeWidth="2" />
					{:else}
						<Clipboard className="size-4" strokeWidth="1.5" />
					{/if}
				</button>
			</Tooltip>
			<Tooltip content={$i18n.t('Download')}>
				<button class={button} aria-label={$i18n.t('Download')} on:click={download}>
					<Download className="size-4" strokeWidth="1.5" />
				</button>
			</Tooltip>
		</div>
	</div>
{:else}
	<div class="size-40 rounded-lg bg-gray-100 dark:bg-gray-850 animate-pulse"></div>
{/if}
