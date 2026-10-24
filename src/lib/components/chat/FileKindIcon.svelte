<script lang="ts">
	// [Gradient] A document's type as a file icon: a page with a coloured label (DOC, XLS, PPT, PDF, ...),
	// named in full in its tooltip.
	export let kind: string;

	const LABELS: Record<string, [string, string]> = {
		DOCX: ['DOC', '#2B579A'],
		XLSX: ['XLS', '#217346'],
		CSV: ['CSV', '#217346'],
		PPTX: ['PPT', '#C43E1C'],
		PDF: ['PDF', '#D93025'],
		PNG: ['IMG', '#7C3AED'],
		JPG: ['IMG', '#7C3AED'],
		SVG: ['SVG', '#7C3AED'],
		ZIP: ['ZIP', '#6B7280']
	};
	$: label = LABELS[kind];
</script>

<span class="shrink-0 inline-flex" title={kind}>
	<svg aria-hidden="true" viewBox="0 0 24 24" class="size-6" xmlns="http://www.w3.org/2000/svg">
		<path
			d="M8 2.75h7l4.25 4.25v13.25a1 1 0 0 1-1 1H8a1 1 0 0 1-1-1V3.75a1 1 0 0 1 1-1Z"
			class="fill-white stroke-gray-400 dark:fill-gray-900 dark:stroke-gray-500"
			stroke-width="1.2"
		/>
		<path
			d="M15 2.75V7h4.25"
			fill="none"
			class="stroke-gray-400 dark:stroke-gray-500"
			stroke-width="1.2"
		/>
		{#if label}
			<rect x="1.75" y="10.5" width="14.5" height="8" rx="1.75" fill={label[1]} />
			<text
				x="9"
				y="16.35"
				text-anchor="middle"
				font-size="5.8"
				font-weight="700"
				fill="white"
				font-family="ui-sans-serif, system-ui, sans-serif">{label[0]}</text
			>
		{:else}
			<path
				d="M10 12.5h6m-6 2.75h6m-6 2.75h4"
				class="stroke-gray-400 dark:stroke-gray-500"
				stroke-width="1.2"
				stroke-linecap="round"
			/>
		{/if}
	</svg>
</span>
