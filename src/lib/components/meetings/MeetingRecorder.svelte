<script lang="ts">
	// [Gradient] Vergadering: records the whole meeting in memory and sends a live segment every ~25 s.
	import { getContext, onDestroy, onMount } from 'svelte';
	import type { Writable } from 'svelte/store';
	import type { i18n as i18nType } from 'i18next';
	import { toast } from 'svelte-sonner';

	import { sendMeetingInput, uploadMeetingAudio } from '$lib/apis/meetings';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import type { AudioRef } from './meeting';
	import {
		RECORDER_MIME_TYPES,
		SegmentRotator,
		SerialQueue,
		sendWhenIdle,
		type RecorderLike
	} from './recorder';

	const i18n: Writable<i18nType> = getContext('i18n');

	let {
		meetingId,
		stream,
		onFinished
	}: {
		meetingId: string;
		stream: MediaStream;
		onFinished: () => void;
	} = $props();

	type Phase = 'recording' | 'stopping' | 'uploading' | 'failed' | 'done';
	let phase = $state<Phase>('recording');
	let seconds = $state(0);
	let pendingChunks = $state(0);
	let levels = $state<number[]>(Array(48).fill(0.02));

	const mimeType = RECORDER_MIME_TYPES.find((type) => MediaRecorder.isTypeSupported(type));
	const extension = (type: string) => (type.split('/')[1] ?? 'webm').split(';')[0].trim() || 'webm';

	let full: MediaRecorder | null = null;
	let fullParts: Blob[] = [];
	let recording: Blob | null = null;
	let rotator: SegmentRotator | null = null;
	let chunkErrorShown = false;
	const queue = new SerialQueue(
		(pending) => (pendingChunks = pending),
		(error) => {
			console.error('Live segment not sent', error);
			if (!chunkErrorShown) {
				chunkErrorShown = true;
				toast.warning($i18n.t('A part of the live transcript could not be sent.'));
			}
		}
	);

	let timer: ReturnType<typeof setInterval> | null = null;
	let wakeLock: { release: () => Promise<void> } | null = null;
	let audioContext: AudioContext | null = null;
	let frame: number | null = null;

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

	const releaseWakeLock = () => {
		wakeLock?.release().catch(() => {});
		wakeLock = null;
	};

	const onVisibility = () => {
		if (document.visibilityState === 'visible' && phase === 'recording') requestWakeLock();
	};

	const onBeforeUnload = (event: BeforeUnloadEvent) => {
		if (phase === 'done') return;
		event.preventDefault();
		event.returnValue = '';
	};

	const visualise = () => {
		audioContext = new AudioContext();
		const analyser = audioContext.createAnalyser();
		audioContext.createMediaStreamSource(stream).connect(analyser);
		const data = new Uint8Array(analyser.fftSize);
		const draw = () => {
			analyser.getByteTimeDomainData(data);
			let sum = 0;
			for (const value of data) sum += ((value - 128) / 128) ** 2;
			const level = Math.min(1, Math.max(0.02, Math.pow(Math.sqrt(sum / data.length) * 10, 1.5)));
			levels = [...levels.slice(1), level];
			frame = requestAnimationFrame(draw);
		};
		frame = requestAnimationFrame(draw);
	};

	const releaseCapture = () => {
		if (timer) clearInterval(timer);
		timer = null;
		if (frame !== null) cancelAnimationFrame(frame);
		frame = null;
		audioContext?.close().catch(() => {});
		audioContext = null;
		for (const track of stream.getTracks()) track.stop();
		releaseWakeLock();
	};

	const stopFull = (): Promise<Blob> =>
		new Promise((resolve) => {
			if (!full || full.state === 'inactive') {
				return resolve(new Blob(fullParts, { type: full?.mimeType || mimeType || 'audio/webm' }));
			}
			const recorder = full;
			recorder.onstop = () =>
				resolve(new Blob(fullParts, { type: recorder.mimeType || mimeType || 'audio/webm' }));
			recorder.stop();
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

	export const stop = async () => {
		if (phase !== 'recording') return;
		phase = 'stopping';
		await rotator?.stop();
		recording = await stopFull();
		releaseCapture();
		await finish();
	};

	onMount(() => {
		full = newRecorder();
		full.ondataavailable = (event) => {
			if (event.data.size > 0) fullParts.push(event.data);
		};
		full.start(10_000);
		rotator = new SegmentRotator(() => newRecorder() as unknown as RecorderLike, sendChunk);
		rotator.start();

		timer = setInterval(() => (seconds += 1), 1000);
		visualise();
		requestWakeLock();
		document.addEventListener('visibilitychange', onVisibility);
		window.addEventListener('beforeunload', onBeforeUnload);
		// Ending the share from the browser bar ends the meeting.
		for (const track of stream.getAudioTracks()) track.addEventListener('ended', () => stop());
	});

	onDestroy(() => {
		document.removeEventListener('visibilitychange', onVisibility);
		window.removeEventListener('beforeunload', onBeforeUnload);
		if (phase === 'recording') {
			rotator?.stop();
			if (full && full.state !== 'inactive') full.stop();
		}
		releaseCapture();
	});

	const clock = (total: number) => {
		const h = Math.floor(total / 3600);
		const m = Math.floor((total % 3600) / 60);
		const s = total % 60;
		const pad = (n: number) => String(n).padStart(2, '0');
		return h > 0 ? `${h}:${pad(m)}:${pad(s)}` : `${pad(m)}:${pad(s)}`;
	};
</script>

<div
	class="flex items-center gap-3 rounded-2xl px-3 py-2 bg-red-50 dark:bg-red-950/30 border border-red-100 dark:border-red-900/40"
>
	<span class="relative flex size-2.5 shrink-0" aria-hidden="true">
		{#if phase === 'recording'}
			<span class="absolute inline-flex size-full animate-ping rounded-full bg-red-400 opacity-75"
			></span>
		{/if}
		<span class="relative inline-flex size-2.5 rounded-full bg-red-500"></span>
	</span>

	<div class="text-sm tabular-nums text-red-700 dark:text-red-300 shrink-0">{clock(seconds)}</div>

	<div class="flex flex-1 items-center gap-[2px] h-6 overflow-hidden" aria-hidden="true">
		{#each levels as level, index (index)}
			<div
				class="w-[3px] shrink-0 rounded-full bg-red-400 dark:bg-red-500"
				style="height: {Math.max(12, level * 100)}%"
			></div>
		{/each}
	</div>

	{#if pendingChunks > 0 && phase === 'recording'}
		<div class="text-xs text-gray-500 shrink-0 hidden sm:block">
			{$i18n.t('Sending live part…')}
		</div>
	{/if}

	{#if phase === 'recording'}
		<button
			type="button"
			class="shrink-0 px-3.5 py-1.5 text-sm rounded-full bg-red-600 hover:bg-red-700 text-white transition"
			onclick={stop}
		>
			{$i18n.t('Stop recording')}
		</button>
	{:else if phase === 'failed'}
		<button
			type="button"
			class="shrink-0 px-3.5 py-1.5 text-sm rounded-full bg-black text-white dark:bg-white dark:text-black transition"
			onclick={finish}
		>
			{$i18n.t('Send recording again')}
		</button>
	{:else}
		<div class="flex items-center gap-2 shrink-0 text-sm text-gray-600 dark:text-gray-300">
			<Spinner className="size-4" />
			{$i18n.t('Uploading recording…')}
		</div>
	{/if}
</div>
