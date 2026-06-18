<script lang="ts">
	import ModelProfile from './ModelProfile.svelte';
</script>

<!--
	Mirrors a ModelItem row's box model (pl-3 pr-1.5 + ml-auto pl-2 pr-1 right block, a
	fixed w-9 slot for the warning/eco badges, then a fixed w-8 trailing slot for the
	menu/check) so the header icons line up exactly above the row meters. Rendered inside
	the scroll container (same width + scrollbar as rows).
-->
<div class="flex w-full items-center pl-3 pr-1.5 py-1">
	<div class="flex-1"></div>

	<div class="ml-auto pl-2 pr-1 flex items-center gap-2 shrink-0">
		<div class="w-9 shrink-0"></div>
		<ModelProfile header />
		<div class="w-8 shrink-0"></div>
	</div>
</div>
