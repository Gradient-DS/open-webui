<script lang="ts">
	import { getContext } from 'svelte';
	import type { Readable } from 'svelte/store';
	import type { i18n as I18n } from 'i18next';
	import { copyToClipboard } from '$lib/utils';
	import { turnErrorDetails, type TurnErrorDetails } from '$lib/utils/turnError';

	const i18n = getContext<Readable<I18n>>('i18n');
	let { error }: { error: unknown } = $props();
	const details: TurnErrorDetails = $derived(turnErrorDetails(error));
	let copied = $state(false);

	const copyTraceId = async () => {
		if (details.trace_id) copied = await copyToClipboard(details.trace_id);
	};
</script>

{#if Object.keys(details).length}
	<details class="text-xs text-gray-500 dark:text-gray-400">
		<summary
			class="cursor-pointer select-none hover:text-gray-700 dark:hover:text-gray-300 hover:underline transition"
		>
			{$i18n.t('Technical details')}
		</summary>
		<dl class="mt-1 flex flex-col gap-1 break-words">
			{#if details.code}
				<div>
					<dt class="inline">{$i18n.t('Platform error code')}:</dt>
					<dd class="inline font-mono">{details.code}</dd>
				</div>
			{/if}
			{#if details.status}
				<div>
					<dt class="inline">{$i18n.t('HTTP status')}:</dt>
					<dd class="inline font-mono">{details.status}</dd>
				</div>
			{/if}
			{#if details.upstream_status}
				<div>
					<dt class="inline">{$i18n.t('Upstream status')}:</dt>
					<dd class="inline font-mono">{details.upstream_status}</dd>
				</div>
			{/if}
			{#if details.trace_id}
				<div>
					<dt class="inline">{$i18n.t('Trace ID')}:</dt>
					<dd class="inline">
						<span class="font-mono">{details.trace_id}</span>
						<button
							type="button"
							class="ml-2 underline hover:text-gray-700 dark:hover:text-gray-300"
							aria-label={$i18n.t('Copy trace ID')}
							onclick={copyTraceId}
						>
							{copied ? $i18n.t('Copied') : $i18n.t('Copy trace ID')}
						</button>
					</dd>
				</div>
			{/if}
		</dl>
	</details>
{/if}
