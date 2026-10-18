<script lang="ts">
	// [Gradient] Vergadering: consent and source choice before anything is recorded (D2, D20).
	import { getContext, onMount, untrack } from 'svelte';
	import type { Writable } from 'svelte/store';
	import type { i18n as i18nType } from 'i18next';

	import Checkbox from '$lib/components/common/Checkbox.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import {
		audioCapabilities,
		listMicrophones,
		unlockMicrophones,
		type AudioSource,
		type Microphone
	} from './audio';

	const i18n: Writable<i18nType> = getContext('i18n');

	let {
		onStart,
		onCancel,
		resume = false
	}: {
		onStart: (source: AudioSource, micId: string | null) => Promise<void>;
		onCancel: () => void;
		/** Continuing an interrupted meeting: its consent stands, only the source is asked. */
		resume?: boolean;
	} = $props();

	let consented = $state(untrack(() => resume));
	let starting = $state(false);

	// Tab audio needs Chromium on a desktop; elsewhere only the microphone is offered.
	const can = audioCapabilities(typeof navigator !== 'undefined' ? navigator : undefined);
	let source = $state<AudioSource>(can.tabAudio ? 'mixed' : 'microphone');

	// The microphone to record: the one chosen last time on this device while it is still there,
	// else the system default. Shown whenever there is more than one, so a display's or headset's
	// microphone is never used unnoticed.
	const MIC_KEY = 'meetings.microphone';
	let microphones = $state<Microphone[]>([]);
	let micId = $state<string | null>(null);
	const named = $derived(microphones.some((mic) => mic.label));
	const refreshMicrophones = async (list?: Microphone[]) => {
		try {
			microphones = list ?? (await listMicrophones());
		} catch {
			microphones = [];
		}
		let saved: string | null = null;
		try {
			saved = localStorage.getItem(MIC_KEY);
		} catch {
			// Storage blocked: the default it is.
		}
		const ids = microphones.map((mic) => mic.id);
		micId = micId && ids.includes(micId) ? micId : saved && ids.includes(saved) ? saved : null;
	};
	const unlock = async () => {
		try {
			await refreshMicrophones(await unlockMicrophones());
		} catch (error) {
			console.error(error);
		}
	};
	const chooseMicrophone = (id: string) => {
		micId = id;
		try {
			localStorage.setItem(MIC_KEY, id);
		} catch {
			// Remembering is a convenience only.
		}
	};
	onMount(() => {
		refreshMicrophones();
		navigator.mediaDevices?.addEventListener?.('devicechange', onDeviceChange);
		return () => navigator.mediaDevices?.removeEventListener?.('devicechange', onDeviceChange);
	});
	const onDeviceChange = () => refreshMicrophones();

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
			await onStart(source, source === 'display' ? null : micId);
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

	{#if source !== 'display' && (named ? microphones.length > 1 : microphones.length > 0)}
		<div class="mt-3 px-2">
			<div class="mb-1 text-xs text-gray-500">{$i18n.t('Microphone')}</div>
			{#if named}
				<select
					class="w-full rounded-lg border border-gray-100 bg-transparent px-2 py-1.5 text-[0.8125rem] text-gray-800 outline-hidden dark:border-gray-850 dark:text-gray-200"
					aria-label={$i18n.t('Microphone')}
					value={micId ?? microphones[0].id}
					onchange={(event) => chooseMicrophone((event.target as HTMLSelectElement).value)}
				>
					{#each microphones as mic (mic.id)}
						<option value={mic.id}>{mic.label}</option>
					{/each}
				</select>
			{:else}
				<button
					type="button"
					class="text-xs text-gray-700 underline underline-offset-2 dark:text-gray-300"
					onclick={unlock}
				>
					{$i18n.t('Choose microphone')}
				</button>
			{/if}
		</div>
	{/if}

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

	{#if !resume}
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
					{$i18n.t(
						'All participants know that this conversation is being recorded and transcribed'
					)}
				</span>
				<span class="block text-xs leading-5 text-gray-500">
					{$i18n.t(
						'The audio is deleted as soon as it has been transcribed. The transcript stays private to you.'
					)}
				</span>
			</span>
		</div>
	{/if}

	<div class="mt-5 flex items-center gap-2 px-2">
		<button
			type="button"
			class="px-3.5 py-1.5 text-sm font-normal bg-black hover:bg-gray-900 text-white dark:bg-white dark:text-black dark:hover:bg-gray-100 transition rounded-full disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-2"
			disabled={!consented || starting}
			onclick={start}
		>
			{#if starting}<Spinner className="size-3.5" />{/if}
			{resume ? $i18n.t('Continue recording') : $i18n.t('Start recording')}
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
