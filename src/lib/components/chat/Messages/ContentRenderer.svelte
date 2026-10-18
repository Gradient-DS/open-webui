<script>
	import { onDestroy, onMount, tick, getContext } from 'svelte';
	const i18n = getContext('i18n');

	import Markdown from './Markdown.svelte';
	import StructuredOutputRenderer from './StructuredOutputRenderer.svelte';
	import { maskInFlightTag } from '$lib/utils/streamMarkup';
	import { DOCUMENT_OPEN } from '$lib/utils/agentDocument';
	import { getOutputText, hasDocumentOutput } from './structuredOutput';
	import {
		artifactCode,
		chatId as currentChatId,
		mobile,
		settings,
		showArtifacts,
		showControls,
		showDocument,
		showEmbeds
	} from '$lib/stores';
	import { isFeatureEnabled } from '$lib/utils/features';
	import FloatingButtons from '../ContentRenderer/FloatingButtons.svelte';
	import { createMessagesList, replaceOutsideCode } from '$lib/utils';
	import { sourceLabels } from './Markdown/sourceLabels'; // [Gradient]

	/**
	 * Extracts all top-level <details>...</details> blocks from content,
	 * handling nested <details> via depth tracking.
	 * Returns { detailsContent, plainContent }.
	 */
	const extractDetailsBlocks = (text) => {
		const blocks = [];
		let remaining = text;
		let result = '';
		const openTag = '<details';
		const closeTag = '</details>';

		while (true) {
			const start = remaining.indexOf(openTag);
			if (start === -1) {
				result += remaining;
				break;
			}

			result += remaining.slice(0, start);

			// Find matching closing tag with depth tracking
			let depth = 1;
			let idx = start + openTag.length;
			while (depth > 0 && idx < remaining.length) {
				if (remaining.startsWith(openTag, idx)) {
					depth++;
				} else if (remaining.startsWith(closeTag, idx)) {
					depth--;
				}
				if (depth > 0) idx++;
			}

			if (depth === 0) {
				const end = idx + closeTag.length;
				blocks.push(remaining.slice(start, end));
				remaining = remaining.slice(end);
			} else {
				// Unmatched opening tag, treat as plain text
				result += remaining.slice(start);
				remaining = '';
				break;
			}
		}

		return {
			detailsContent: blocks.join('\n'),
			plainContent: result.trim()
		};
	};

	export let id;
	export let chatId = '';
	export let content;
	/** @type {import('./structuredOutput').OutputItem[]} */
	export let output = [];

	export let history;
	export let messageId;

	export let selectedModels = [];

	export let done = true;
	export let model = null;
	/** @type {import('./Citations/reduceSources').RawSource[] | null} */
	export let sources = null;

	export let save = false;
	export let preview = false;
	export let compactPreview = false;
	export let floatingButtons = true;

	export let editCodeBlock = true;
	export let topPadding = false;
	export let allowEmbeds = false;

	export let onSave = (e) => {};
	export let onSourceClick = (e) => {};
	export let onTaskClick = (e) => {};
	export let onToolCallResolved = (e) => {};
	export let onSetInputText = (text) => {};

	let contentContainerElement;
	let floatingButtonsElement;
	let floatingButtonsRange = null;

	let sourceIds = [];
	$: getSourceIds(sources);

	let documentDetected = false;
	$: {
		// [Gradient] Document Writer: content is persisted empty since v0.10.2,
		// so documents are detected from the output items too — otherwise the
		// side panel never auto-opens for a reloaded chat.
		const hasDocument =
			hasDocumentOutput(output) ||
			DOCUMENT_OPEN.test(getOutputText(output)) ||
			(typeof content === 'string' &&
				(DOCUMENT_OPEN.test(content) ||
					/<details\b[^>]*\btype="document"/.test(content) ||
					/<details\b[^>]*\btype="tool_calls"[^>]*\bname="write_document"/.test(content)));

		if (
			hasDocument &&
			!documentDetected &&
			isFeatureEnabled('document_writer') &&
			($settings?.detectDocuments ?? true) &&
			!$mobile &&
			$currentChatId
		) {
			documentDetected = true;
			tick().then(() => {
				showDocument.set(true);
				showControls.set(true);
			});
		}

		if (!hasDocument) {
			documentDetected = false;
		}
	}

	// [Gradient] One label per document, as the agent numbers them: a name shared by two documents no longer
	// shifts the later chips onto the wrong (or no) source.
	const getSourceIds = (sources) => {
		sourceIds = sourceLabels(sources, model?.info?.meta?.capabilities?.citations == false);
	};

	/** @param {string} messageContent */
	const formatMessageContent = (messageContent) =>
		model?.info?.meta?.capabilities?.citations == false
			? replaceOutsideCode(messageContent, (segment) =>
					segment.replace(/\s*(\[(?:\d+(?:#[^,\]\s]+)?(?:,\s*\d+(?:#[^,\]\s]+)?)*)\])+/g, '')
				)
			: messageContent;

	let autoOpenedArtifactIds = new Set();

	const hasClosingCodeFence = (raw = '') => /(?:^|\n)```[ \t]*$/.test(raw.trimEnd());

	const markdownUpdateHandler = /** @type {any} */ (
		async (
			/** @type {{ lang?: string; raw?: string; text?: string }} */ token,
			codeBlockId = ''
		) => {
			const { lang = '', raw = '', text: code = '' } = token;
			const normalizedLang = lang.toLowerCase();
			const isArtifact =
				['html', 'svg'].includes(normalizedLang) ||
				(normalizedLang === 'xml' && code.toLowerCase().includes('<svg'));
			const artifactId = codeBlockId || `${normalizedLang}:${raw}`;

			if (
				isFeatureEnabled('artifacts') &&
				($settings?.detectArtifacts ?? false) &&
				!compactPreview &&
				isArtifact &&
				hasClosingCodeFence(raw) &&
				!autoOpenedArtifactIds.has(artifactId) &&
				!$mobile &&
				$currentChatId
			) {
				autoOpenedArtifactIds.add(artifactId);
				await tick();
				showArtifacts.set(true);
				showControls.set(true);
			}
		}
	);

	const previewHandler = /** @type {any} */ (
		async (/** @type {string} */ value) => {
			console.log('Preview', value);
			await artifactCode.set(/** @type {any} */ (value));
			await showControls.set(true);
			await showArtifacts.set(true);
			await showEmbeds.set(false);
		}
	);

	const updateButtonPosition = (event) => {
		const buttonsContainerElement = document.getElementById(`floating-buttons-${id}`);
		if (
			!contentContainerElement?.contains(event.target) &&
			!buttonsContainerElement?.contains(event.target)
		) {
			closeFloatingButtons();
			return;
		}

		setTimeout(async () => {
			await tick();

			if (!contentContainerElement?.contains(event.target)) return;

			let selection = window.getSelection();

			if (selection.toString().trim().length > 0) {
				floatingButtonsRange = selection.getRangeAt(0);
				positionFloatingButtons();
			} else {
				closeFloatingButtons();
			}
		}, 0);
	};

	const positionFloatingButtons = () => {
		const buttonsContainerElement = document.getElementById(`floating-buttons-${id}`);
		if (!floatingButtonsRange || !buttonsContainerElement) return;

		const rect = floatingButtonsRange.getBoundingClientRect();
		const parentRect = contentContainerElement.getBoundingClientRect();

		buttonsContainerElement.style.display = 'block';

		// Calculate space available on the right
		const spaceOnRight = parentRect.right - rect.left;
		let halfScreenWidth = $mobile ? window.innerWidth / 2 : window.innerWidth / 3;

		if (spaceOnRight < halfScreenWidth) {
			const right = document.documentElement.clientWidth - rect.right;
			buttonsContainerElement.style.right = `${right}px`;
			buttonsContainerElement.style.left = 'auto'; // Reset left
		} else {
			// Enough space, position using 'left'
			buttonsContainerElement.style.left = `${rect.left}px`;
			buttonsContainerElement.style.right = 'auto'; // Reset right
		}
		buttonsContainerElement.style.top = `${rect.bottom + 5}px`; // +5 to add some spacing
	};

	const closeFloatingButtons = () => {
		floatingButtonsRange = null;
		const buttonsContainerElement = document.getElementById(`floating-buttons-${id}`);
		if (buttonsContainerElement) {
			buttonsContainerElement.style.display = 'none';
		}

		if (floatingButtonsElement) {
			// check if closeHandler is defined

			if (typeof floatingButtonsElement?.closeHandler === 'function') {
				// call the closeHandler function
				floatingButtonsElement?.closeHandler();
			}
		}
	};

	const keydownHandler = (e) => {
		if (e.key === 'Escape') {
			closeFloatingButtons();
		}
	};

	// Reactive listener attachment: re-attaches when floatingButtons
	// transitions from false → true (e.g. when message.done flips).
	let listenersAttached = false;

	function attachListeners() {
		if (!listenersAttached && contentContainerElement) {
			contentContainerElement.addEventListener('mouseup', updateButtonPosition);
			document.addEventListener('mouseup', updateButtonPosition);
			document.addEventListener('keydown', keydownHandler);
			listenersAttached = true;
		}
	}

	function detachListeners() {
		if (listenersAttached) {
			contentContainerElement?.removeEventListener('mouseup', updateButtonPosition);
			document.removeEventListener('mouseup', updateButtonPosition);
			document.removeEventListener('keydown', keydownHandler);
			listenersAttached = false;
		}
	}

	$: if (floatingButtons && contentContainerElement) {
		attachListeners();
	} else {
		detachListeners();
	}

	onDestroy(() => {
		detachListeners();
	});
</script>

<svelte:window on:scroll|capture={positionFloatingButtons} on:resize={positionFloatingButtons} />

<div bind:this={contentContainerElement}>
	{#if output?.length}
		<StructuredOutputRenderer
			{id}
			{chatId}
			{messageId}
			{output}
			{model}
			{save}
			{preview}
			{compactPreview}
			{done}
			{allowEmbeds}
			{editCodeBlock}
			{topPadding}
			{sourceIds}
			renderMarkdown={$settings?.renderMarkdownInAssistantMessages ?? true}
			{formatMessageContent}
			{onSourceClick}
			{onTaskClick}
			{onToolCallResolved}
			{onSave}
			onUpdate={markdownUpdateHandler}
			onPreview={previewHandler}
		/>
	{:else if ($settings?.renderMarkdownInAssistantMessages ?? true) || DOCUMENT_OPEN.test(content)}
		<div class="markdown-prose">
			<Markdown
				{id}
				{chatId}
				{messageId}
				content={formatMessageContent(content)}
				{model}
				{save}
				{preview}
				{compactPreview}
				{done}
				{allowEmbeds}
				{editCodeBlock}
				{topPadding}
				{sourceIds}
				{onSourceClick}
				{onTaskClick}
				{onToolCallResolved}
				{onSave}
				onUpdate={markdownUpdateHandler}
				onPreview={previewHandler}
			/>
		</div>
	{:else}
		{@const extracted = extractDetailsBlocks(content)}

		{#if extracted.detailsContent}
			<!-- Render structural blocks (tool calls, reasoning, etc.) through Markdown -->
			<div class="markdown-prose">
				<Markdown
					{id}
					{chatId}
					{messageId}
					content={extracted.detailsContent}
					{save}
					{preview}
					{compactPreview}
					{done}
					{allowEmbeds}
					{onToolCallResolved}
				/>
			</div>
		{/if}
		{#if extracted.plainContent}
			<div class="whitespace-pre-wrap text-[0.9375rem]">
				{maskInFlightTag(extracted.plainContent)}
			</div>
		{/if}
	{/if}
</div>

{#if floatingButtons}
	<FloatingButtons
		bind:this={floatingButtonsElement}
		{id}
		actions={$settings?.floatingActionButtons ?? []}
		onSetInputText={(text) => {
			onSetInputText(text);
			closeFloatingButtons();
		}}
	/>
{/if}
