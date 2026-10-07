<script lang="ts">
	// [Gradient] Vergadering: records the whole meeting in memory and sends a live segment at the first
	// pause after 20 s (45 s at most). Looks like the chat dictation pill (VoiceRecording.svelte).
	import { getContext, onDestroy, onMount, untrack } from 'svelte';
	import type { Writable } from 'svelte/store';
	import type { i18n as i18nType } from 'i18next';
	import { toast } from 'svelte-sonner';

	import { sendMeetingInput, uploadMeetingAudio } from '$lib/apis/meetings';
	import ConfirmDialog from '$lib/components/common/ConfirmDialog.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import XMark from '$lib/components/icons/XMark.svelte';
	import type { Capture } from './audio';
	import { formatTimestamp, type AudioRef } from './meeting';
	import {
		RECORDER_MIME_TYPES,
		SegmentRotator,
		SerialQueue,
		decideCut,
		initialCutState,
		rmsOf,
		sendWhenIdle,
		type RecorderLike
	} from './recorder';

	const i18n: Writable<i18nType> = getContext('i18n');

	let {
		meetingId,
		capture,
		onFinished,
		onDiscard
	}: {
		meetingId: string;
		capture: Capture;
		onFinished: () => void;
		onDiscard: () => void;
	} = $props();

	type Phase = 'recording' | 'stopping' | 'uploading' | 'failed' | 'done';
	let phase = $state<Phase>('recording');
	let seconds = $state(0);
	let showDiscard = $state(false);
	let barCount = $state(120);
	let levels = $state<number[]>(Array(120).fill(0));

	// One recorder per capture: the view remounts it for a new one.
	const stream = untrack(() => capture.stream);
	const mimeType = RECORDER_MIME_TYPES.find((type) => MediaRecorder.isTypeSupported(type));
	const extension = (type: string) => (type.split('/')[1] ?? 'webm').split(';')[0].trim() || 'webm';

	let full: MediaRecorder | null = null;
	let fullParts: Blob[] = [];
	let recording: Blob | null = null;
	let rotator: SegmentRotator | null = null;
	let chunkErrorShown = false;
	const queue = new SerialQueue(undefined, (error) => {
		console.error('Live segment not sent', error);
		if (!chunkErrorShown) {
			chunkErrorShown = true;
			toast.warning($i18n.t('A part of the live transcript could not be sent.'));
		}
	});

	let clock: ReturnType<typeof setInterval> | null = null;
	let sampler: ReturnType<typeof setInterval> | null = null;
	let frame: number | null = null;
	let audioContext: AudioContext | null = null;
	let wakeLock: { release: () => Promise<void> } | null = null;
	let resizeObserver: ResizeObserver | null = null;

	const newRecorder = (): MediaRecorder =>
		new MediaRecorder(stream, mimeType ? { mimeType } : undefined);

	const upload = async (blob: Blob, name: string): Promise<AudioRef> =>
		(await uploadMeetingAudio(localStorage.token, blob, name)) as AudioRef;

	const sendChunk = (blob: Blob, seq: number) =>
		queue.push(async () => {
			const audio_ref = await upload(blob, `chunk-${seq}.${extension(blob.type)}`);
			await sendWhenIdle(() =>
				sendMeetingInput(localStorage.token, meetingId, { type: 'chunk', seq, audio_ref })
			);
		});

	const requestWakeLock = async () => {
		try {
			wakeLock = (await navigator.wakeLock?.request('screen')) ?? null;
		} catch {
			wakeLock = null;
		}
	};

	const onVisibility = () => {
		if (document.visibilityState === 'visible' && phase === 'recording') requestWakeLock();
	};

	const onBeforeUnload = (event: BeforeUnloadEvent) => {
		if (phase === 'done') return;
		event.preventDefault();
		event.returnValue = '';
	};

	// Same scaling as the dictation visualiser.
	const normalize = (rms: number) => Math.min(1, Math.max(0.01, Math.pow(rms * 10, 1.5)));

	const listen = () => {
		audioContext = new AudioContext();
		const analyser = audioContext.createAnalyser();
		analyser.minDecibels = -45;
		audioContext.createMediaStreamSource(stream).connect(analyser);
		const data = new Uint8Array(analyser.fftSize);

		// Cuts are decided on a timer, not on animation frames: those stop in a background tab,
		// which is exactly where the user is during an online meeting.
		let cut = initialCutState(performance.now());
		sampler = setInterval(() => {
			analyser.getByteTimeDomainData(data);
			const decision = decideCut(cut, performance.now(), rmsOf(data));
			cut = decision.state;
			if (decision.cut) rotator?.rotate();
		}, 100);

		const draw = () => {
			analyser.getByteTimeDomainData(data);
			levels = [...levels, normalize(rmsOf(data))].slice(-barCount);
			frame = requestAnimationFrame(draw);
		};
		frame = requestAnimationFrame(draw);
	};

	const releaseCapture = () => {
		for (const handle of [clock, sampler]) if (handle) clearInterval(handle);
		clock = sampler = null;
		if (frame !== null) cancelAnimationFrame(frame);
		frame = null;
		audioContext?.close().catch(() => {});
		audioContext = null;
		capture.release();
		wakeLock?.release().catch(() => {});
		wakeLock = null;
	};

	const stopFull = (): Promise<Blob> =>
		new Promise((resolve) => {
			const type = full?.mimeType || mimeType || 'audio/webm';
			if (!full || full.state === 'inactive') return resolve(new Blob(fullParts, { type }));
			full.onstop = () => resolve(new Blob(fullParts, { type }));
			full.stop();
		});

	const finish = async () => {
		phase = 'uploading';
		try {
			await queue.drain();
			const blob = recording as Blob;
			const audio_ref = await upload(blob, `meeting.${extension(blob.type)}`);
			await sendWhenIdle(() =>
				sendMeetingInput(localStorage.token, meetingId, { type: 'finish', audio_ref })
			);
			recording = null;
			phase = 'done';
			onFinished();
		} catch (error) {
			console.error('Recording not sent', error);
			phase = 'failed';
			toast.error(`${(error as Error)?.message ?? error}`);
		}
	};

	const stop = async () => {
		if (phase !== 'recording') return;
		phase = 'stopping';
		await rotator?.stop();
		recording = await stopFull();
		releaseCapture();
		await finish();
	};

	const discard = async () => {
		phase = 'done';
		await rotator?.stop();
		if (full && full.state !== 'inactive') full.stop();
		releaseCapture();
		fullParts = [];
		recording = null;
		onDiscard();
	};

	onMount(() => {
		full = newRecorder();
		full.ondataavailable = (event) => {
			if (event.data.size > 0) fullParts.push(event.data);
		};
		full.start(10_000);
		rotator = new SegmentRotator(() => newRecorder() as unknown as RecorderLike, sendChunk);
		rotator.start();

		clock = setInterval(() => (seconds += 1), 1000);
		listen();
		requestWakeLock();
		document.addEventListener('visibilitychange', onVisibility);
		window.addEventListener('beforeunload', onBeforeUnload);
		resizeObserver = new ResizeObserver(() => {
			barCount = Math.max(40, Math.floor(window.innerWidth / 4));
		});
		resizeObserver.observe(document.body);
		// Ending the share from the browser bar ends the meeting.
		for (const track of capture.tracks) track.addEventListener('ended', () => stop());
	});

	onDestroy(() => {
		document.removeEventListener('visibilitychange', onVisibility);
		window.removeEventListener('beforeunload', onBeforeUnload);
		resizeObserver?.disconnect();
		if (phase === 'recording') {
			rotator?.stop();
			if (full && full.state !== 'inactive') full.stop();
		}
		releaseCapture();
	});

	const busy = $derived(phase === 'stopping' || phase === 'uploading');
</script>

<ConfirmDialog
	bind:show={showDiscard}
	title={$i18n.t('Discard this recording?')}
	message={$i18n.t('The recording and its live transcript are deleted. This cannot be undone.')}
	confirmLabel={$i18n.t('Discard')}
	onConfirm={discard}
/>

<div
	class="{busy
		? ' bg-gray-100/50 dark:bg-gray-850/50'
		: 'bg-indigo-300/10 dark:bg-indigo-500/10 '} rounded-full flex justify-between w-full"
>
	<div class="flex items-center mr-1">
		<button
			type="button"
			class="p-1.5 {busy
				? ' bg-gray-200 dark:bg-gray-700/50'
				: 'bg-indigo-400/20 text-indigo-600 dark:text-indigo-300 '} rounded-full"
			aria-label={$i18n.t('Discard recording')}
			disabled={busy}
			onclick={() => (showDiscard = true)}
		>
			<XMark className={'size-4'} />
		</button>
	</div>

	<div
		class="flex flex-1 self-center items-center justify-between ml-2 mx-1 overflow-hidden h-6"
		dir="rtl"
	>
		<div
			class="flex items-center gap-0.5 h-6 w-full max-w-full overflow-hidden overflow-x-hidden flex-wrap"
		>
			{#each levels.slice().reverse() as rms, index (index)}
				<div class="flex items-center h-full">
					<div
						class="w-[0.125rem] shrink-0 {busy
							? ' bg-gray-500 dark:bg-gray-400 '
							: 'bg-indigo-500 dark:bg-indigo-400 '} inline-block h-full"
						style="height: {Math.min(100, Math.max(14, rms * 100))}%;"
					></div>
				</div>
			{/each}
		</div>
	</div>

	<div class="flex">
		<div class="mx-1.5 pr-1 flex justify-center items-center">
			<div
				class="text-sm {busy
					? ' text-gray-500 dark:text-gray-400 '
					: ' text-indigo-400 '} font-normal flex-1 mx-auto text-center tabular-nums"
			>
				{formatTimestamp(seconds)}
			</div>
		</div>

		<div class="flex items-center">
			{#if phase === 'recording'}
				<button
					type="button"
					aria-label={$i18n.t('Stop recording')}
					class="p-1.5 bg-indigo-500 text-white dark:bg-indigo-500 dark:text-blue-950 rounded-full"
					onclick={stop}
				>
					<svg
						xmlns="http://www.w3.org/2000/svg"
						fill="none"
						viewBox="0 0 24 24"
						stroke-width="2.5"
						stroke="currentColor"
						class="size-4"
					>
						<path stroke-linecap="round" stroke-linejoin="round" d="m4.5 12.75 6 6 9-13.5" />
					</svg>
				</button>
			{:else if phase === 'failed'}
				<button
					type="button"
					class="px-3 py-1 text-xs bg-indigo-500 text-white dark:text-blue-950 rounded-full"
					onclick={finish}
				>
					{$i18n.t('Send recording again')}
				</button>
			{:else}
				<div class="p-1.5 text-gray-500"><Spinner className="size-4" /></div>
			{/if}
		</div>
	</div>
</div>
