<script lang="ts">
	// [Gradient] Vergadering: consent and source choice before anything is recorded (D2, D20).
	import { getContext } from 'svelte';
	import type { Writable } from 'svelte/store';
	import type { i18n as i18nType } from 'i18next';

	import Modal from '$lib/components/common/Modal.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import Mic from '$lib/components/icons/Mic.svelte';
	import Computer from '$lib/components/icons/Computer.svelte';
	import type { AudioSource } from './meeting';

	const i18n: Writable<i18nType> = getContext('i18n');

	let {
		show = $bindable(false),
		onStart,
		onCancel = () => {}
	}: {
		show?: boolean;
		onStart: (choice: { title: string; source: AudioSource }) => Promise<void>;
		onCancel?: () => void;
	} = $props();

	let title = $state('');
	let consented = $state(false);
	let source = $state<AudioSource>('microphone');
	let starting = $state(false);
	let wasShown = false;

	const displaySupported =
		typeof navigator !== 'undefined' && !!navigator.mediaDevices?.getDisplayMedia;

	$effect(() => {
		// The modal closes itself on Escape or an outside click; that is a cancel.
		if (show) wasShown = true;
		else if (wasShown && !starting) {
			wasShown = false;
			onCancel();
		}
	});

	const start = async () => {
		if (!consented || starting) return;
		starting = true;
		try {
			await onStart({ title: title.trim(), source });
			// A started meeting closes the dialog; that close is not a cancel.
			wasShown = false;
		} finally {
			starting = false;
		}
	};
</script>

<Modal bind:show size="sm">
	<div class="px-5 pt-4 pb-5 dark:text-gray-200">
		<div class="text-lg font-medium mb-3">{$i18n.t('New meeting')}</div>

		<label class="block text-xs text-gray-500 mb-1" for="meeting-title">{$i18n.t('Title')}</label>
		<input
			id="meeting-title"
			class="w-full rounded-lg px-3 py-2 text-sm bg-gray-50 dark:bg-gray-850 outline-hidden"
			placeholder={$i18n.t('Meeting title (optional)')}
			maxlength="200"
			bind:value={title}
		/>

		<div class="mt-4 text-xs text-gray-500 mb-1">{$i18n.t('Record from')}</div>
		<div class="grid grid-cols-2 gap-2">
			<button
				type="button"
				class="flex items-center gap-2 rounded-xl border px-3 py-2 text-sm text-left transition {source ===
				'microphone'
					? 'border-gray-900 dark:border-gray-100'
					: 'border-gray-200 dark:border-gray-800'}"
				aria-pressed={source === 'microphone'}
				onclick={() => (source = 'microphone')}
			>
				<Mic className="size-4 shrink-0" />
				<span>{$i18n.t('Microphone')}</span>
			</button>
			<button
				type="button"
				class="flex items-center gap-2 rounded-xl border px-3 py-2 text-sm text-left transition disabled:opacity-50 {source ===
				'display'
					? 'border-gray-900 dark:border-gray-100'
					: 'border-gray-200 dark:border-gray-800'}"
				aria-pressed={source === 'display'}
				disabled={!displaySupported}
				onclick={() => (source = 'display')}
			>
				<Computer className="size-4 shrink-0" />
				<span>{$i18n.t('Tab, window or app audio')}</span>
			</button>
		</div>
		{#if source === 'display'}
			<div class="mt-1.5 text-xs text-gray-500">
				{$i18n.t('Share the tab or window of your call and turn on sharing its audio.')}
			</div>
		{/if}

		<label class="mt-4 flex items-start gap-2 text-sm cursor-pointer">
			<input type="checkbox" class="mt-0.5" bind:checked={consented} />
			<span
				>{$i18n.t(
					'All participants know that this conversation is being recorded and transcribed'
				)}</span
			>
		</label>
		<div class="mt-1 ml-6 text-xs text-gray-500">
			{$i18n.t(
				'The audio is deleted as soon as it has been transcribed. The transcript stays private to you.'
			)}
		</div>

		<div class="mt-5 flex justify-end gap-2">
			<button
				type="button"
				class="px-3.5 py-1.5 text-sm rounded-full bg-gray-100 hover:bg-gray-200 dark:bg-gray-850 dark:hover:bg-gray-800 transition"
				onclick={() => (show = false)}
			>
				{$i18n.t('Cancel')}
			</button>
			<button
				type="button"
				class="px-3.5 py-1.5 text-sm rounded-full bg-black text-white dark:bg-white dark:text-black transition disabled:opacity-40 flex items-center gap-2"
				disabled={!consented || starting}
				onclick={start}
			>
				{#if starting}<Spinner className="size-3.5" />{/if}
				{$i18n.t('Start recording')}
			</button>
		</div>
	</div>
</Modal>
