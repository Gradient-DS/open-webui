<script lang="ts">
	import DOMPurify from 'dompurify';
	import type { Token } from 'marked';
	import type { Writable } from 'svelte/store';
	import type { i18n as i18nType } from 'i18next';

	import { getContext } from 'svelte';

	import { WEBUI_BASE_URL } from '$lib/constants';
	import { settings } from '$lib/stores';
	import { withoutRemoteLoads } from '$lib/utils/remoteLoads';
	import { externalHost } from '$lib/utils/safeImageUrl';

	const i18n = getContext<Writable<i18nType>>('i18n');

	export let id: string;
	export let token: Token;

	let html: string | null = null;

	$: text = token.type === 'html' ? token?.text : null;
	$: html = text ? DOMPurify.sanitize(text) : null;

	// [Gradient] Media and pages from another host load only once the user clicks: rendering them would hand
	// whatever their URL carries (a prompt-injected answer can put chat content there) to that host.
	let revealed: string[] = [];
	const held = (src: string | null | undefined): string | null =>
		src && !revealed.includes(src) ? externalHost(src) : null;
	const reveal = (src: string) => (revealed = [...revealed, src]);
</script>

{#snippet hold(src: string, host: string, label: string)}
	<button
		type="button"
		class="my-2 inline-flex max-w-full items-center gap-1.5 overflow-hidden rounded-lg border border-gray-100/50 bg-gray-50/40 px-2 py-1 text-xs text-gray-500 hover:text-gray-700 dark:border-white/[0.04] dark:bg-white/[0.03] dark:text-gray-400 dark:hover:text-gray-200"
		title={src}
		on:click={() => reveal(src)}
	>
		<span class="truncate">{$i18n.t(label, { host })}</span>
	</button>
{/snippet}

{#if token.type === 'html'}
	{#if html && html.includes('<video')}
		{@const video = html.match(/<video[^>]*>([\s\S]*?)<\/video>/)}
		{@const videoSrc = video && video[1]}
		{#if videoSrc && held(videoSrc.replaceAll('&amp;', '&'))}
			{@render hold(
				videoSrc.replaceAll('&amp;', '&'),
				held(videoSrc.replaceAll('&amp;', '&')) ?? '',
				'Load media from {{host}}'
			)}
		{:else if videoSrc}
			<!-- svelte-ignore a11y-media-has-caption -->
			<video
				class="w-full my-2"
				src={videoSrc.replaceAll('&amp;', '&')}
				title="Video player"
				frameborder="0"
				referrerpolicy="strict-origin-when-cross-origin"
				controls
				allowfullscreen
			></video>
		{:else}
			{token.text}
		{/if}
	{:else if html && html.includes('<audio')}
		{@const audio = html.match(/<audio[^>]*>([\s\S]*?)<\/audio>/)}
		{@const audioSrc = audio && audio[1]}
		{#if audioSrc && held(audioSrc.replaceAll('&amp;', '&'))}
			{@render hold(
				audioSrc.replaceAll('&amp;', '&'),
				held(audioSrc.replaceAll('&amp;', '&')) ?? '',
				'Load media from {{host}}'
			)}
		{:else if audioSrc}
			<!-- svelte-ignore a11y-media-has-caption -->
			<audio
				class="w-full my-2"
				src={audioSrc.replaceAll('&amp;', '&')}
				title="Audio player"
				controls
			></audio>
		{:else}
			{token.text}
		{/if}
	{:else if token.text && token.text.match(/<iframe\s+[^>]*src="https:\/\/www\.youtube\.com\/embed\/([a-zA-Z0-9_-]{11})(?:\?[^"]*)?"[^>]*><\/iframe>/)}
		{@const match = token.text.match(
			/<iframe\s+[^>]*src="https:\/\/www\.youtube\.com\/embed\/([a-zA-Z0-9_-]{11})(?:\?[^"]*)?"[^>]*><\/iframe>/
		)}
		{@const ytId = match && match[1]}
		{#if ytId}
			<iframe
				class="w-full aspect-video my-2"
				src={`https://www.youtube.com/embed/${ytId}`}
				title="YouTube video player"
				frameborder="0"
				allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share"
				referrerpolicy="strict-origin-when-cross-origin"
				allowfullscreen
			>
			</iframe>
		{/if}
	{:else if token.text && token.text.includes('<iframe')}
		{@const match = token.text.match(/<iframe\s+[^>]*src="([^"]+)"[^>]*><\/iframe>/)}
		{@const iframeSrc = match && match[1]}
		{#if iframeSrc && held(iframeSrc)}
			{@render hold(iframeSrc, held(iframeSrc) ?? '', 'Load embedded content from {{host}}')}
		{:else if iframeSrc}
			<iframe
				class="w-full my-2"
				src={iframeSrc}
				title="Embedded content"
				frameborder="0"
				sandbox
				on:load={(e) => {
					try {
						e.currentTarget.style.height =
							e.currentTarget.contentWindow.document.body.scrollHeight + 20 + 'px';
					} catch {}
				}}
			></iframe>
		{:else}
			{token.text}
		{/if}
	{:else if token.text && token.text.includes('<status')}
		{@const match = token.text.match(/<status title="([^"]+)" done="(true|false)" ?\/?>/)}
		{@const statusTitle = match && match[1]}
		{@const statusDone = match && match[2] === 'true'}
		{#if statusTitle}
			<div class="flex flex-col justify-center -space-y-0.5">
				<div
					class="{statusDone === false
						? 'shimmer'
						: ''} text-gray-500 dark:text-gray-500 line-clamp-1 text-wrap"
				>
					{statusTitle}
				</div>
			</div>
		{:else}
			{token.text}
		{/if}
	{:else if token.text.includes(`<file type="html"`)}
		{@const match = token.text.match(/<file type="html" id="([^"]+)"/)}
		{@const fileId = match && match[1]}
		{#if fileId}
			<iframe
				class="w-full my-2"
				src={`${WEBUI_BASE_URL}/api/v1/files/${fileId}/content/html`}
				title="Content"
				frameborder="0"
				sandbox="{($settings?.iframeSandboxAllowScripts ?? true)
					? 'allow-scripts'
					: ''}{($settings?.iframeSandboxAllowDownloads ?? true)
					? ' allow-downloads'
					: ''}{($settings?.iframeSandboxAllowForms ?? true)
					? ' allow-forms'
					: ''}{($settings?.iframeSandboxAllowSameOrigin ?? false) ? ' allow-same-origin' : ''}"
				referrerpolicy="strict-origin-when-cross-origin"
				allowfullscreen
				width="100%"
				on:load={(e) => {
					try {
						e.currentTarget.style.height =
							e.currentTarget.contentWindow.document.body.scrollHeight + 20 + 'px';
					} catch {}
				}}
			></iframe>
		{/if}
	{:else if token.text.trim().match(/^<br\s*\/?>$/i)}
		<br />
	{:else if html && html.includes('<table')}
		<!-- [Gradient] Render raw HTML <table> blocks (e.g. agent-generated
		     documents that copy a table verbatim from a source) instead of
		     falling through to escaped text. ``html`` is already
		     DOMPurify-sanitized above, so {@html} is XSS-safe; withoutRemoteLoads keeps it from
		     fetching other hosts (images, CSS url()) on render. -->
		<div class="markdown-html-table scrollbar-hidden relative overflow-x-auto max-w-full my-2">
			<!-- eslint-disable-next-line svelte/no-at-html-tags — `html` is DOMPurify-sanitized above -->
			{@html withoutRemoteLoads(html)}
		</div>
	{:else}
		{token.text}
	{/if}
{/if}

<style>
	:global(.markdown-html-table table) {
		width: 100%;
		border-collapse: collapse;
		font-size: 0.875rem;
		line-height: 1.25rem;
	}
	:global(.markdown-html-table th),
	:global(.markdown-html-table td) {
		border: 1px solid rgb(229 231 235);
		padding: 0.375rem 0.625rem;
		text-align: left;
		vertical-align: top;
	}
	:global(.dark .markdown-html-table th),
	:global(.dark .markdown-html-table td) {
		border-color: rgb(55 65 81);
	}
	:global(.markdown-html-table th) {
		font-weight: 600;
	}
	:global(.markdown-html-table p) {
		margin: 0;
	}
</style>
