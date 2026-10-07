<script lang="ts">
	// [Gradient] Vergadering: consent and source choice before anything is recorded (D2, D20).
	import { getContext } from 'svelte';
	import type { Writable } from 'svelte/store';
	import type { i18n as i18nType } from 'i18next';

	import Checkbox from '$lib/components/common/Checkbox.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import { audioCapabilities, type AudioSource } from './audio';

	const i18n: Writable<i18nType> = getContext('i18n');

	let {
		onStart,
		onCancel
	}: {
		onStart: (source: AudioSource) => Promise<void>;
		onCancel: () => void;
	} = $props();

	let consented = $state(false);
	let starting = $state(false);

	// Tab audio needs Chromium on a desktop; elsewhere only the microphone is offered.
	const can = audioCapabilities(typeof navigator !== 'undefined' ? navigator : undefined);
	let source = $state<AudioSource>(can.tabAudio ? 'mixed' : 'microphone');

	const options = $derived(
		[
			{
				value: 'mixed' as AudioSource,
				label: $i18n.t('Microphone + tab'),
				help: $i18n.t(
					'For online meetings. The tab only carries the other participants, so your microphone adds your own voice. Use headphones to avoid echo.'
				),
				needsDisplay: true
			},
			{
				value: 'microphone' as AudioSource,
				label: $i18n.t('Microphone'),
				help: $i18n.t('For a meeting in the room.'),
				needsDisplay: false
			},
			{
				value: 'display' as AudioSource,
				label: $i18n.t('Tab or window only'),
				help: $i18n.t('Only the audio of a shared browser tab, without your microphone.'),
				needsDisplay: true
			}
		].filter((option) => can.tabAudio || !option.needsDisplay)
	);

	const start = async () => {
		if (!consented || starting) return;
		starting = true;
		try {
			await onStart(source);
		} finally {
			starting = false;
		}
	};
</script>

<div class="w-full py-2 text-sm">
	<div class="mb-1 text-xs text-gray-500">{$i18n.t('Record from')}</div>
	<div class="flex flex-col gap-0.5" role="radiogroup" aria-label={$i18n.t('Record from')}>
		{#each options as option (option.value)}
			<button
				type="button"
				role="radio"
				aria-checked={source === option.value}
				class="flex w-full items-start gap-2.5 rounded-xl px-2 py-1.5 text-left transition {source ===
				option.value
					? 'bg-gray-50 dark:bg-gray-850'
					: 'hover:bg-gray-50 dark:hover:bg-gray-850'}"
				onclick={() => (source = option.value)}
			>
				<span
					class="mt-1 size-3 shrink-0 rounded-full outline outline-[1.5px] -outline-offset-1 {source ===
					option.value
						? 'bg-black outline-black dark:bg-white dark:outline-white'
						: 'outline-gray-300 dark:outline-gray-600'}"
				></span>
				<span class="min-w-0">
					<span class="block text-[0.8125rem] text-gray-800 dark:text-gray-200">{option.label}</span
					>
					<span class="block text-xs leading-5 text-gray-500">{option.help}</span>
				</span>
			</button>
		{/each}
	</div>

	{#if !can.tabAudio}
		<div class="mt-2 px-2 text-xs leading-5 text-gray-500">
			{$i18n.t(
				'Recording an online meeting from a browser tab works in Chrome or Edge on a computer.'
			)}
		</div>
	{:else if source !== 'microphone'}
		<div class="mt-2 px-2 text-xs leading-5 text-gray-500">
			{$i18n.t('Choose the browser tab of your call and turn on "Share tab audio".')}
			{#if can.windows}
				{$i18n.t(
					'Using the Teams app? Then choose "Entire screen" and turn on "Share system audio".'
				)}
			{/if}
		</div>
	{/if}

	<div
		class="mt-4 flex cursor-pointer items-start gap-2.5 px-2"
		role="checkbox"
		tabindex="0"
		aria-checked={consented}
		onclick={() => (consented = !consented)}
		onkeydown={(event) => {
			if (event.key === ' ' || event.key === 'Enter') {
				event.preventDefault();
				consented = !consented;
			}
		}}
	>
		<span class="mt-0.5 pointer-events-none">
			<Checkbox state={consented ? 'checked' : 'unchecked'} />
		</span>
		<span>
			<span class="block text-[0.8125rem] text-gray-800 dark:text-gray-200">
				{$i18n.t('All participants know that this conversation is being recorded and transcribed')}
			</span>
			<span class="block text-xs leading-5 text-gray-500">
				{$i18n.t(
					'The audio is deleted as soon as it has been transcribed. The transcript stays private to you.'
				)}
			</span>
		</span>
	</div>

	<div class="mt-5 flex items-center gap-2 px-2">
		<button
			type="button"
			class="px-3.5 py-1.5 text-sm font-normal bg-black hover:bg-gray-900 text-white dark:bg-white dark:text-black dark:hover:bg-gray-100 transition rounded-full disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-2"
			disabled={!consented || starting}
			onclick={start}
		>
			{#if starting}<Spinner className="size-3.5" />{/if}
			{$i18n.t('Start recording')}
		</button>
		<button
			type="button"
			class="px-3.5 py-1.5 text-sm font-normal text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-850 transition rounded-full"
			onclick={onCancel}
		>
			{$i18n.t('Cancel')}
		</button>
	</div>

	{#if can.mobile}
		<div class="mt-2 px-2 text-xs leading-5 text-gray-500">
			{$i18n.t('Keep your screen on during the recording.')}
		</div>
	{/if}
</div>
