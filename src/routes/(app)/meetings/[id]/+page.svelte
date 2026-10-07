<script lang="ts">
	// [Gradient] Vergadering: one meeting; `new` starts one after consent.
	import { page } from '$app/stores';
	import MeetingView from '$lib/components/meetings/MeetingView.svelte';
</script>

{#key $page.params.id}
	<MeetingView id={$page.params.id ?? 'new'} />
{/key}
