<script lang="ts">
	// [Gradient] A delivered CSV as a table, and text, markdown or JSON as text, in the document panel.
	import type { Writable } from 'svelte/store';
	import type { i18n as i18nType } from 'i18next';
	import { getContext } from 'svelte';

	import { parseCsv, type CsvTable } from '$lib/utils/csvTable';
	import { loadText, type DeliveredFile } from '$lib/utils/deliveredFile';

	export let file: DeliveredFile;
	export let chatId: string;

	const i18n = getContext<Writable<i18nType>>('i18n');
	// A column whose values are all numbers, in either decimal notation, is aligned right like a spreadsheet's.
	const NUMBER = /^-?[\d.,]+%?$/;
	$: numeric = (table?.header ?? []).map(
		(_, column) =>
			(table?.rows ?? []).some((row) => (row[column] ?? '') !== '') &&
			(table?.rows ?? []).every((row) => !row[column] || NUMBER.test(row[column]))
	);

	let text: string | null = null;
	let table: CsvTable | null = null;
	let failed = false;
	let loadedFor: DeliveredFile | undefined;

	$: if (file !== loadedFor) load(file);

	const load = async (wanted: DeliveredFile) => {
		loadedFor = wanted;
		text = null;
		table = null;
		failed = false;
		try {
			const loaded = await loadText(wanted, chatId);
			if (loadedFor !== wanted) return;
			if (wanted.content_type === 'text/csv') table = parseCsv(loaded);
			else text = loaded;
		} catch (e) {
			console.error(e);
			failed = true;
		}
	};
</script>

{#if failed}
	<div class="m-auto text-xs text-red-500">
		{$i18n.t('Failed to load the preview. Download the file instead.')}
	</div>
{:else if table}
	<div class="h-full overflow-auto px-3 pb-3">
		<table class="min-w-full text-xs text-left border-collapse">
			<thead class="sticky top-0 bg-white dark:bg-gray-900">
				<tr>
					{#each table.header as cell, column}
						<th
							class="px-2 py-1.5 font-semibold text-gray-900 dark:text-white border-b border-gray-200 dark:border-gray-700 whitespace-nowrap {numeric[
								column
							]
								? 'text-right'
								: ''}">{cell}</th
						>
					{/each}
				</tr>
			</thead>
			<tbody>
				{#each table.rows as row}
					<tr class="hover:bg-gray-50 dark:hover:bg-gray-850">
						{#each row as cell, column}
							<td
								class="px-2 py-1 text-gray-700 dark:text-gray-300 border-b border-gray-100 dark:border-gray-800 whitespace-nowrap tabular-nums {numeric[
									column
								]
									? 'text-right'
									: ''}">{cell}</td
							>
						{/each}
					</tr>
				{/each}
			</tbody>
		</table>
		{#if table.truncated}
			<p class="pt-2 text-xs text-gray-500 dark:text-gray-400">
				{$i18n.t('Showing the first {{count}} rows. Download the file to see all of them.', {
					count: table.rows.length
				})}
			</p>
		{/if}
	</div>
{:else if text !== null}
	<pre
		class="h-full overflow-auto px-4 pb-4 text-xs text-gray-800 dark:text-gray-200 whitespace-pre-wrap">{text}</pre>
{/if}
