<script lang="ts">
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

	// Agent unreachable: skip to a blank simple editor (graceful degradation).
	const handleUnavailable = () => {
		draft = null;
		step = 'review';
	};
</script>

{#if step === 'interview'}
	<InterviewChat onComplete={handleComplete} onUnavailable={handleUnavailable} />
{:else}
	<SimpleModelEditor {draft} model={null} edit={false} {onSubmit} {onAdvanced} />
{/if}
