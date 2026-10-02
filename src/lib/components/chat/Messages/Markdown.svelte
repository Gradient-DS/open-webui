<script context="module">
	import { marked } from 'marked';

	import markedExtension from '$lib/utils/marked/extension';
	import markedKatexExtension from '$lib/utils/marked/katex-extension';
	import { disableSingleTilde } from '$lib/utils/marked/strikethrough-extension';
	import { mentionExtension } from '$lib/utils/marked/mention-extension';
	import colonFenceExtension from '$lib/utils/marked/colon-fence-extension';
	import footnoteExtension from '$lib/utils/marked/footnote-extension';
	import citationExtension, { applyCitationWalker } from '$lib/utils/marked/citation-extension';

	const options = {
		throwOnError: false
	};

	marked.use(markedKatexExtension(options));
	marked.use(markedExtension(options));
	marked.use(citationExtension(options));
	marked.use(footnoteExtension(options));
	marked.use(colonFenceExtension(options));
	marked.use(disableSingleTilde);
	marked.use({
		extensions: [
			mentionExtension({ triggerChar: '@' }),
			mentionExtension({ triggerChar: '#' }),
			mentionExtension({ triggerChar: '$' })
		]
	});

</script>

<script>
	import { onDestroy } from 'svelte';
	import { replaceTokens, processResponseContent } from '$lib/utils';
	import { maskInFlightTag, markupSafeEnd } from '$lib/utils/streamMarkup';
	import { user } from '$lib/stores';

	import MarkdownTokens from './Markdown/MarkdownTokens.svelte';

	export let id = '';
	export let chatId = '';
	export let messageId = '';
	export let content;
	export let done = true;
	export let model = null;
	export let save = false;
	export let preview = false;
	export let compactPreview = false;

	export let paragraphTag = 'p';
	export let editCodeBlock = true;
	export let topPadding = false;
	export let allowEmbeds = true;

	export let sourceIds = [];

	export let onSave = () => {};
	export let onUpdate = () => {};

	export let onPreview = () => {};

	export let onSourceClick = () => {};
	export let onTaskClick = () => {};
	export let onToolCallResolved = () => {};

	let tokens = [];
	let pendingUpdate = null;
	let lastContent = '';
	let lastParsedContent = '';

	// [Gradient] Streamed text arrives in bursts of a few hundred characters every few
	// hundred milliseconds, and showing each burst at once reads as jerky. While a
	// message streams, the shown text catches up with what arrived over
	// REVEAL_FRAMES animation frames, about one burst interval, so it flows. A
	// message that is already done when it mounts renders at once.
	const REVEAL_FRAMES = 18;
	let revealing = false;
	let shown = 0;
	let framesLeft = 0;
	// What the tokens render as: done only once the reveal has caught up.
	let settled = done;

	const revealedText = () => {
		const end = markupSafeEnd(content, Math.min(shown, content.length));
		// Keep a jump past a whole block, so the next frame does not shrink the text.
		shown = Math.max(shown, end);
		return content.slice(0, end);
	};

	const parseTokens = (text = content, final = done) => {
		// [Gradient] A pipeline tag the model is still typing (`<document
		// title="Gesch`) is not a token yet, so marked lexes it as literal text and
		// it flashes in the bubble until its `>` arrives. Mask that tail while
		// streaming; the `done` parse always sees the raw content.
		const source = final ? text : maskInFlightTag(text);
		if (source === lastContent) return;
		lastContent = source;

		const processed = replaceTokens(processResponseContent(source), model?.name, $user?.name);
		if (processed === lastParsedContent) return;
		lastParsedContent = processed;

		tokens = applyCitationWalker(marked.lexer(processed));
	};

	const revealStep = () => {
		pendingUpdate = null;
		const remaining = content.length - shown;
		if (remaining > 0) {
			shown += Math.ceil(remaining / Math.max(1, framesLeft));
			framesLeft -= 1;
		}
		if (shown >= content.length && done) {
			revealing = false;
			settled = true;
			parseTokens();
			return;
		}
		parseTokens(revealedText(), false);
		if (shown < content.length) pendingUpdate = requestAnimationFrame(revealStep);
	};

	const updateHandler = (content, done) => {
		if (!content) return;
		if (!done && !revealing) {
			revealing = true;
			settled = false;
		}
		if (!revealing) {
			cancelAnimationFrame(pendingUpdate);
			pendingUpdate = null;
			settled = done;
			parseTokens();
			return;
		}
		// Content replaced by a shorter one (a regenerate, a rewrite) restarts from there.
		shown = Math.min(shown, content.length);
		framesLeft = REVEAL_FRAMES;
		if (!pendingUpdate) pendingUpdate = requestAnimationFrame(revealStep);
	};

	// `done` is passed in rather than closed over so it is a dependency of this
	// statement: the final parse has to run even when the turn ends without another
	// content delta, or the masked tail would stay hidden.
	$: updateHandler(content, done);

	onDestroy(() => {
		cancelAnimationFrame(pendingUpdate);
	});
</script>

{#key id}
	<MarkdownTokens
		{tokens}
		{id}
		{chatId}
		{messageId}
		done={settled}
		{save}
		{preview}
		{compactPreview}
		{paragraphTag}
		{editCodeBlock}
		{sourceIds}
		{topPadding}
		{allowEmbeds}
		{onTaskClick}
		{onSourceClick}
		{onToolCallResolved}
		{onSave}
		{onUpdate}
		{onPreview}
	/>
{/key}
