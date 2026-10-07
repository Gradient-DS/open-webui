<script lang="ts">
	// [Gradient] Vergadering: list of the caller's meetings.
	import { showSidebar } from '$lib/stores';
	import Meetings from '$lib/components/meetings/Meetings.svelte';
</script>

<div
	class="flex flex-col w-full h-screen max-h-[100dvh] transition-width duration-200 ease-in-out {$showSidebar
		? 'md:max-w-[calc(100%-var(--sidebar-width))]'
		: ''} max-w-full"
>
	<div class="flex-1 max-h-full overflow-y-auto">
		<Meetings />
	</div>
</div>
