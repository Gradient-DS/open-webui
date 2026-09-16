<script lang="ts">
	import { showEmbeds, showArtifacts, showCallOverlay, showControls } from '$lib/stores';
	import { openSourcesTabSignal } from '$lib/stores/citations';

	export let activeTab: 'controls' | 'files' | 'overview' | 'document' | 'sources';
	export let controlsWidth: number;
	export let largeScreen: boolean;

	// [Gradient] Citation clicks select Sources even when the panel is already open.
	// Only react to signals raised after this instance mounted: the store keeps its
	// count across chat switches, and a fresh ChatControls must not pop the panel
	// open just because a citation was clicked in an earlier chat.
	let handledSourcesSignal = $openSourcesTabSignal;
	$: if ($openSourcesTabSignal !== handledSourcesSignal) {
		handledSourcesSignal = $openSourcesTabSignal;
		activeTab = 'sources';
		showEmbeds.set(false);
		showArtifacts.set(false);
		showCallOverlay.set(false);
		showControls.set(true);
		widenSourcesPanel();
	}

	// [Gradient] Keep width changes from retriggering the tab-opening signal.
	function widenSourcesPanel() {
		if (largeScreen && controlsWidth < 560) controlsWidth = 620;
	}
</script>
