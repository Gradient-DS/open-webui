<script lang="ts">
	import { fade, fly } from 'svelte/transition';
	import { cubicOut } from 'svelte/easing';

	import InterviewChat from './Simple/InterviewChat.svelte';
	import SimpleModelEditor from './SimpleModelEditor.svelte';

	export let onSubmit: (info: any) => Promise<void> | void;
	export let onAdvanced: () => void;

	// 'interview' -> chat with the onboarding agent; 'review' -> simple editor.
	let step: 'interview' | 'review' = 'interview';
	let draft: any = null;

	const handleComplete = (d: any) => {
		draft = d;
		step = 'review';
	};

	// Used both when the user skips the interview ("Set up manually") and
	// when the onboarding agent is unreachable — either way, drop straight
	// into a blank simple editor.
	const goToManualSetup = () => {
		draft = null;
		step = 'review';
	};
</script>

<!-- Both views are absolutely stacked in this sized container (the workspace
     content area gives it a definite height) so the interview cross-fades
     into the builder in place, without a layout jump. -->
<div class="relative w-full h-full">
	{#if step === 'interview'}
		<div class="absolute inset-0" out:fade={{ duration: 150 }}>
			<InterviewChat
				onComplete={handleComplete}
				onUnavailable={goToManualSetup}
				onSkip={goToManualSetup}
			/>
		</div>
	{:else}
		<div
			class="absolute inset-0 overflow-y-auto"
			in:fly={{ y: 12, duration: 260, delay: 80, easing: cubicOut }}
		>
			<SimpleModelEditor {draft} model={null} edit={false} {onSubmit} {onAdvanced} />
		</div>
	{/if}
</div>
