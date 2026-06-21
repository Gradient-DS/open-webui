<script lang="ts">
	import ModelProfile from './ModelProfile.svelte';
</script>

<!--
	The right-side axis legend (Quality/Speed). Mirrors a ModelItem row's right block — a
	fixed w-9 slot for the warning/eco badges, the meters, then a fixed w-8 trailing slot for
	the menu/check — so the header icons line up exactly above the row meters. Rendered inline
	at the right end of the tabs row (see Selector.svelte).
-->
<div class="pl-2 pr-1 flex items-center gap-2 shrink-0">
	<div class="w-9 shrink-0"></div>
	<ModelProfile header />
	<div class="w-8 shrink-0"></div>
</div>
