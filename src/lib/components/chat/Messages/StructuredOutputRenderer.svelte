<script lang="ts">
	import Collapsible from '$lib/components/common/Collapsible.svelte';
	import ToolCallDisplay from '$lib/components/common/ToolCallDisplay.svelte';
	import { settings } from '$lib/stores';

	import Markdown from './Markdown.svelte';
	import ConsecutiveDetailsGroup from './Markdown/ConsecutiveDetailsGroup.svelte';
	import {
		buildOutputDisplayItems,
		type OutputDetailToken,
		type OutputDisplayItem,
		type OutputItem
	} from './structuredOutput';

	export let id = '';
	export let output: OutputItem[] = [];
	export let done = true;
	export let model = null;
	export let save = false;
	export let preview = false;
	export let renderMarkdown = true;
	export let editCodeBlock = true;
	export let topPadding = false;
	export let sourceIds: string[] = [];
	export let formatMessageContent: (content: string) => string = (content) => content;
	export let onSave: any = () => {};
	export let onSourceClick: any = () => {};
	export let onTaskClick: any = () => {};
	export let onUpdate: any = () => {};
	export let onPreview: any = () => {};

	const getDetailTitle = (detailToken: OutputDetailToken): any => detailToken.summary;
	const getDetailAttributes = (detailToken: OutputDetailToken): any => detailToken.attributes;

	// [Gradient] Reasoning output items are consumed by StatusHistory /
	// ReasoningBullet in ResponseMessage — the fork's canonical display
	// surface for reasoning — mirroring the MarkdownTokens carve-out for
	// <details type="reasoning"> blocks. Rendering them here too would show
	// duplicate "Thought for N seconds" collapsibles detached from their
	// chronological position between the tool statuses they separated.
	// [Gradient] Tool calls are carved out for the same reason as reasoning:
	// StatusHistory renders the tool activity, with translated per-tool status
	// bullets. Rendering them here as well showed a ToolCallDisplay dropdown
	// (tool name + arguments) for the duration of the stream that then
	// disappeared once the content path took over. The carve-out lives inside
	// buildOutputDisplayItems rather than in this filter because it has to spare
	// tool calls whose result carries embeds or files, and that pairing is only
	// resolvable there (the attachments hang off the function_call_output item).
	$: displayItems = buildOutputDisplayItems(
		output.filter((item) => item?.type !== 'reasoning'),
		{ hideToolCalls: true }
	) as OutputDisplayItem[];
</script>

{#each displayItems as displayItem (displayItem.id)}
	{#if displayItem.type === 'message'}
		{#if renderMarkdown}
			<Markdown
				id={`${id}-${displayItem.id}`}
				content={formatMessageContent(displayItem.text)}
				{model}
				{save}
				{preview}
				{done}
				{editCodeBlock}
				{topPadding}
				{sourceIds}
				{onSourceClick}
				{onTaskClick}
				{onSave}
				{onUpdate}
				{onPreview}
			/>
		{:else}
			<div class="whitespace-pre-wrap">{displayItem.text}</div>
		{/if}
	{:else if displayItem.type === 'document'}
		<!-- [Gradient] Document Writer: the serialized <details type="document">
		     block routes through Markdown → MarkdownTokens → DocumentCard, the
		     same rendering the pre-v0.10.2 content path produced. -->
		{#if renderMarkdown}
			<Markdown
				id={`${id}-${displayItem.id}`}
				content={formatMessageContent(displayItem.text)}
				{model}
				{save}
				{preview}
				{done}
				{editCodeBlock}
				{topPadding}
				{sourceIds}
				{onSourceClick}
				{onTaskClick}
				{onSave}
				{onUpdate}
				{onPreview}
			/>
		{:else}
			<div class="whitespace-pre-wrap">{displayItem.text}</div>
		{/if}
	{:else if displayItem.type === 'detail_group'}
		<ConsecutiveDetailsGroup
			id={`${id}-${displayItem.id}`}
			tokens={displayItem.tokens}
			messageDone={done}
		>
			<div slot="content" class="space-y-1">
				{#each displayItem.tokens as detailToken, detailIndex}
					{#if detailToken.attributes?.type === 'tool_calls'}
						<ToolCallDisplay
							id={`${id}-${displayItem.id}-${detailIndex}-tool-call`}
							attributes={detailToken.attributes}
							resultContent={detailToken.text}
							grouped={true}
							open={$settings?.expandDetails ?? false}
							className="w-full space-y-1"
						/>
					{:else if detailToken.text?.length > 0}
						<Collapsible
							title={getDetailTitle(detailToken)}
							open={$settings?.expandDetails ?? false}
							attributes={getDetailAttributes(detailToken)}
							messageDone={done}
							className="w-full space-y-1"
						>
							<div class="mb-1.5" slot="content">
								<Markdown
									id={`${id}-${displayItem.id}-${detailIndex}-detail`}
									content={detailToken.text}
									{done}
									{editCodeBlock}
								/>
							</div>
						</Collapsible>
					{:else}
						<Collapsible
							title={getDetailTitle(detailToken)}
							open={false}
							disabled={true}
							attributes={getDetailAttributes(detailToken)}
							messageDone={done}
							className="w-full space-y-1"
						/>
					{/if}
				{/each}
			</div>
		</ConsecutiveDetailsGroup>
	{:else}
		{@const detailToken = displayItem.token}
		{#if detailToken.attributes?.type === 'tool_calls'}
			<ToolCallDisplay
				id={`${id}-${displayItem.id}-tool-call`}
				attributes={detailToken.attributes}
				resultContent={detailToken.text}
				open={$settings?.expandDetails ?? false}
				className="w-full space-y-1"
			/>
		{:else if detailToken.text?.length > 0}
			<Collapsible
				title={getDetailTitle(detailToken)}
				open={$settings?.expandDetails ?? false}
				attributes={getDetailAttributes(detailToken)}
				messageDone={done}
				className="w-full space-y-1"
			>
				<div class="mb-1.5" slot="content">
					<Markdown
						id={`${id}-${displayItem.id}-detail`}
						content={detailToken.text}
						{done}
						{editCodeBlock}
					/>
				</div>
			</Collapsible>
		{:else}
			<Collapsible
				title={getDetailTitle(detailToken)}
				open={false}
				disabled={true}
				attributes={getDetailAttributes(detailToken)}
				messageDone={done}
				className="w-full space-y-1"
			/>
		{/if}
	{/if}
{/each}
