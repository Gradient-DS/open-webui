<script lang="ts">
	// [Gradient] Vergadering: one meeting, rendered from the agent's latest meeting_state snapshot.
	import { getContext, onDestroy, onMount, tick, untrack } from 'svelte';
	import type { Writable } from 'svelte/store';
	import type { i18n as i18nType } from 'i18next';
	import { toast } from 'svelte-sonner';
	import DOMPurify from 'dompurify';
	import { marked } from 'marked';

	import { beforeNavigate, goto, replaceState } from '$app/navigation';
	import dayjs from '$lib/dayjs';
	import { showSidebar } from '$lib/stores';
	import { deleteMeeting, getMeeting, sendMeetingInput, startMeeting } from '$lib/apis/meetings';
	import { printDocument } from '$lib/utils/documentPrint';

	import ConfirmDialog from '$lib/components/common/ConfirmDialog.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import Download from '$lib/components/icons/Download.svelte';
	import GarbageBin from '$lib/components/icons/GarbageBin.svelte';
	import ArrowLeft from '$lib/components/icons/ArrowLeft.svelte';
	import ExclamationTriangle from '$lib/components/icons/ExclamationTriangle.svelte';

	import ConsentDialog from './ConsentDialog.svelte';
	import MeetingRecorder from './MeetingRecorder.svelte';
	import { markdownToDocx } from './docx';
	import { sendWhenIdle } from './recorder';
	import {
		CONSENT_TEXT_VERSION,
		OUTPUT_KINDS,
		fileStem,
		formatTimestamp,
		groupTurns,
		liveText,
		outputMarkdown,
		speakerName,
		transcriptMarkdown,
		type AudioSource,
		type ExportLabels,
		type MeetingRead,
		type MeetingState,
		type OutputKind
	} from './meeting';

	const i18n: Writable<i18nType> = getContext('i18n');

	let { id }: { id: string } = $props();
	// The route remounts this view per id; `new` stays local after the shallow URL update.
	const initialId = untrack(() => id);

	type Tab = 'transcript' | OutputKind;

	let meetingId = $state<string | null>(initialId === 'new' ? null : initialId);
	let read = $state<MeetingRead | null>(null);
	let loading = $state(initialId !== 'new');
	let stream = $state<MediaStream | null>(null);
	let localTitle = $state('');
	let showConsent = $state(initialId === 'new');
	let showDelete = $state(false);
	let showDownload = $state(false);
	let showRaw = $state(false);
	let tab = $state<Tab>('transcript');
	let pendingAction = $state<{ kind: OutputKind; before: string; since: number } | null>(null);
	let sending = $state(false);
	let editingSpeaker = $state<string | null>(null);
	let speakerDraft = $state('');
	// Set once this page sent `finish`; the agent's next snapshot may lag behind its turn.
	let finishedHere = $state(false);
	let pollTimer: ReturnType<typeof setTimeout> | null = null;
	let destroyed = false;

	const meeting: MeetingState | null = $derived(read?.state ?? null);
	const threadBusy = $derived(read?.status === 'running');
	const title = $derived(
		meeting?.title ||
			localTitle ||
			$i18n.t('Meeting of {{date}}', {
				date: dayjs(meeting?.consent?.at ?? undefined).format('LL')
			})
	);
	const speakers = $derived(meeting?.transcript?.speakers ?? []);
	const turns = $derived(groupTurns(meeting?.transcript?.segments ?? [], showRaw));
	const awaitingTranscript = $derived(
		meeting?.status === 'transcribing' ||
			(meeting?.status === 'recording' && !stream && (threadBusy || finishedHere))
	);
	const interrupted = $derived(
		meeting?.status === 'recording' && !stream && !threadBusy && !finishedHere
	);

	const labels = (): ExportLabels => ({
		transcript: $i18n.t('Transcript'),
		summary: $i18n.t('Summary'),
		minutes: $i18n.t('Minutes'),
		actions: $i18n.t('Action items'),
		owner: $i18n.t('Owner'),
		due: $i18n.t('Due'),
		noActions: $i18n.t('No action items')
	});

	const tabLabel = (value: Tab) =>
		value === 'transcript' ? $i18n.t('Transcript') : labels()[value];

	const outputKey = (snapshot: MeetingState | null, kind: OutputKind) =>
		JSON.stringify(snapshot?.outputs?.[kind] ?? null);

	const load = async () => {
		if (!meetingId) return;
		try {
			read = await getMeeting(localStorage.token, meetingId);
		} catch (error) {
			const status = (error as { status?: number })?.status;
			if (status === 404) {
				toast.error($i18n.t('This meeting no longer exists.'));
				goto('/meetings');
				return;
			}
			console.error(error);
		} finally {
			loading = false;
		}
		if (pendingAction) {
			const changed = outputKey(meeting, pendingAction.kind) !== pendingAction.before;
			const failed = meeting?.error?.stage === 'action';
			const stale = Date.now() - pendingAction.since > 10 * 60_000;
			if (!threadBusy && (changed || failed || stale)) pendingAction = null;
		}
	};

	const pollDelay = (): number | null => {
		if (stream) return 5000;
		if (sending || pendingAction || threadBusy || awaitingTranscript) return 2000;
		if (!meeting && meetingId) return 2000;
		return null;
	};

	const schedule = () => {
		if (pollTimer) clearTimeout(pollTimer);
		pollTimer = null;
		const delay = pollDelay();
		if (delay === null || destroyed) return;
		pollTimer = setTimeout(async () => {
			await load();
			schedule();
		}, delay);
	};

	const send = async (input: Parameters<typeof sendMeetingInput>[2]) => {
		if (!meetingId) return false;
		sending = true;
		try {
			await sendWhenIdle(() => sendMeetingInput(localStorage.token, meetingId as string, input));
			return true;
		} catch (error) {
			toast.error(`${(error as Error)?.message ?? error}`);
			return false;
		} finally {
			sending = false;
			await load();
			schedule();
		}
	};

	const acquire = async (source: AudioSource): Promise<MediaStream> => {
		if (source === 'microphone') {
			return navigator.mediaDevices.getUserMedia({
				audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true }
			});
		}
		// As in dictation: share a tab, window or screen and keep only its audio.
		const shared = await navigator.mediaDevices.getDisplayMedia({ audio: true });
		for (const track of shared.getVideoTracks()) track.stop();
		const audio = shared.getAudioTracks();
		if (audio.length === 0) throw new Error($i18n.t('The shared tab or window has no audio.'));
		return new MediaStream(audio);
	};

	const start = async ({ title: chosen, source }: { title: string; source: AudioSource }) => {
		let captured: MediaStream;
		try {
			captured = await acquire(source);
		} catch (error) {
			console.error(error);
			toast.error(
				(error as Error)?.message && (error as Error).name === 'Error'
					? (error as Error).message
					: $i18n.t('Error accessing media devices.')
			);
			return;
		}
		try {
			const res = await startMeeting(localStorage.token, {
				type: 'start',
				...(chosen ? { title: chosen } : {}),
				consent: { text_version: CONSENT_TEXT_VERSION, at: new Date().toISOString() }
			});
			meetingId = res.id;
			localTitle = chosen;
			stream = captured;
			showConsent = false;
			replaceState(`/meetings/${encodeURIComponent(res.id)}`, {});
			schedule();
		} catch (error) {
			for (const track of captured.getTracks()) track.stop();
			toast.error(`${(error as Error)?.message ?? error}`);
		}
	};

	const recorded = async () => {
		finishedHere = true;
		stream = null;
		await load();
		schedule();
	};

	const runAction = async (kind: OutputKind) => {
		tab = kind;
		pendingAction = { kind, before: outputKey(meeting, kind), since: Date.now() };
		if (!(await send({ type: 'action', kind, template_id: null }))) pendingAction = null;
	};

	const saveSpeaker = async (label: string) => {
		const name = speakerDraft.trim();
		editingSpeaker = null;
		if (!name || name === speakerName(speakers, label)) return;
		await send({ type: 'rename_speaker', label, name });
	};

	const remove = async () => {
		if (!meetingId) return;
		try {
			await deleteMeeting(localStorage.token, meetingId);
			toast.success($i18n.t('Meeting deleted'));
			goto('/meetings');
		} catch (error) {
			toast.error(`${(error as Error)?.message ?? error}`);
		}
	};

	const exportMarkdown = (): string | null => {
		if (!meeting) return null;
		return tab === 'transcript'
			? transcriptMarkdown(title, meeting, labels(), showRaw)
			: outputMarkdown(title, meeting, tab, labels());
	};

	const download = async (format: 'md' | 'docx' | 'pdf') => {
		showDownload = false;
		const markdown = exportMarkdown();
		if (!markdown) return;
		const name = `${fileStem(title)}-${fileStem(tabLabel(tab))}`;
		try {
			if (format === 'md') {
				saveAs(new Blob([markdown], { type: 'text/markdown;charset=utf-8' }), `${name}.md`);
			} else if (format === 'docx') {
				saveAs(await markdownToDocx(markdown), `${name}.docx`);
			} else {
				await printDocument(name, markdown, 'markdown');
			}
		} catch (error) {
			console.error(error);
			toast.error($i18n.t('Download failed'));
		}
	};

	const saveAs = (blob: Blob, name: string) => {
		const url = URL.createObjectURL(blob);
		const link = Object.assign(document.createElement('a'), { href: url, download: name });
		document.body.append(link);
		link.click();
		link.remove();
		setTimeout(() => URL.revokeObjectURL(url), 1000);
	};

	const renderMarkdown = (markdown: string) =>
		DOMPurify.sanitize(marked.parse(markdown, { async: false }) as string);

	beforeNavigate(({ cancel, type }) => {
		if (stream && type !== 'leave' && !confirm($i18n.t('Stop this recording and leave?'))) cancel();
	});

	onMount(async () => {
		if (meetingId) {
			await load();
			schedule();
		}
	});

	onDestroy(() => {
		destroyed = true;
		if (pollTimer) clearTimeout(pollTimer);
	});

	$effect(() => {
		// Re-arm polling whenever the snapshot or local activity changes what we wait for.
		void [read, pendingAction, sending, stream];
		tick().then(schedule);
	});
</script>

<ConsentDialog bind:show={showConsent} onStart={start} onCancel={() => goto('/meetings')} />

<ConfirmDialog
	bind:show={showDelete}
	title={$i18n.t('Delete meeting?')}
	message={$i18n.t(
		'The transcript and all outputs of this meeting are deleted. This cannot be undone.'
	)}
	confirmLabel={$i18n.t('Delete')}
	onConfirm={remove}
/>

<div
	class="flex flex-col w-full h-screen max-h-[100dvh] transition-width duration-200 ease-in-out {$showSidebar
		? 'md:max-w-[calc(100%-var(--sidebar-width))]'
		: ''} max-w-full"
>
	<div class="flex-1 max-h-full overflow-y-auto">
		<div class="max-w-4xl mx-auto w-full px-4 pt-3 pb-10">
			<div class="flex items-center gap-2 mb-3">
				<a
					href="/meetings"
					class="p-1.5 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-850 transition"
					aria-label={$i18n.t('All meetings')}
				>
					<ArrowLeft className="size-4" />
				</a>
				<h1 class="flex-1 text-lg font-medium line-clamp-1 dark:text-gray-100">{title}</h1>

				{#if meeting?.status === 'ready'}
					<div class="relative">
						<Tooltip content={$i18n.t('Download')}>
							<button
								type="button"
								class="p-1.5 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-850 transition"
								aria-label={$i18n.t('Download')}
								aria-expanded={showDownload}
								onclick={() => (showDownload = !showDownload)}
							>
								<Download className="size-4" />
							</button>
						</Tooltip>
						{#if showDownload}
							<div
								class="absolute right-0 z-20 mt-1 w-56 rounded-2xl p-1 shadow-lg bg-white dark:bg-gray-850 border border-gray-100 dark:border-gray-800"
							>
								<div class="px-3 py-1 text-xs text-gray-500">{tabLabel(tab)}</div>
								{#each [['md', $i18n.t('Markdown (.md)')], ['docx', $i18n.t('Word document (.docx)')], ['pdf', $i18n.t('PDF document (.pdf)')]] as [format, label] (format)}
									<button
										type="button"
										class="w-full text-left px-3 py-1.5 text-sm rounded-xl hover:bg-gray-50 dark:hover:bg-gray-800"
										disabled={!exportMarkdown()}
										onclick={() => download(format as 'md' | 'docx' | 'pdf')}
									>
										{label}
									</button>
								{/each}
							</div>
						{/if}
					</div>
				{/if}

				{#if meetingId && !stream}
					<Tooltip content={$i18n.t('Delete meeting')}>
						<button
							type="button"
							class="p-1.5 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-850 transition"
							aria-label={$i18n.t('Delete meeting')}
							onclick={() => (showDelete = true)}
						>
							<GarbageBin className="size-4" />
						</button>
					</Tooltip>
				{/if}
			</div>

			{#if stream && meetingId}
				<MeetingRecorder {meetingId} {stream} onFinished={recorded} />
				<div class="mt-4">
					<div class="text-xs text-gray-500 mb-1">{$i18n.t('Live transcript (rough)')}</div>
					<div class="text-sm text-gray-700 dark:text-gray-300 whitespace-pre-wrap leading-relaxed">
						{liveText(meeting) || $i18n.t('The first part appears after about half a minute.')}
					</div>
				</div>
			{:else if loading}
				<div class="flex justify-center py-16"><Spinner className="size-5" /></div>
			{:else if !meetingId}
				<div class="py-16 text-center text-sm text-gray-500">
					{$i18n.t('Confirm consent to start recording.')}
				</div>
			{:else}
				{#if meeting?.error}
					<div
						class="flex items-start gap-2 rounded-xl px-3 py-2 mb-3 text-sm bg-yellow-50 text-yellow-900 dark:bg-yellow-950/30 dark:text-yellow-200"
						role="alert"
					>
						<ExclamationTriangle className="size-4 mt-0.5 shrink-0" />
						<div class="flex-1">{meeting.error.message}</div>
						{#if meeting.error.retryable}
							<button
								type="button"
								class="shrink-0 px-3 py-1 rounded-full text-xs bg-yellow-900 text-white dark:bg-yellow-200 dark:text-yellow-950 disabled:opacity-50"
								disabled={sending || threadBusy}
								onclick={() => send({ type: 'retry' })}
							>
								{$i18n.t('Try again')}
							</button>
						{/if}
					</div>
				{/if}

				{#if interrupted}
					<div
						class="rounded-xl px-3 py-2 mb-3 text-sm bg-gray-50 dark:bg-gray-850 text-gray-600 dark:text-gray-300"
					>
						{$i18n.t('This recording was interrupted and cannot be completed. You can delete it.')}
					</div>
				{:else if !meeting || awaitingTranscript}
					<div class="flex flex-col items-center gap-3 py-16 text-sm text-gray-500">
						<Spinner className="size-5" />
						{$i18n.t('Making the transcript. This takes about a minute per hour of recording.')}
					</div>
				{:else if meeting.status === 'ready'}
					<div class="flex flex-wrap items-center gap-2 mb-3">
						{#each OUTPUT_KINDS as kind (kind)}
							<button
								type="button"
								class="flex items-center gap-1.5 px-3.5 py-1.5 text-sm rounded-full border border-gray-200 dark:border-gray-800 hover:bg-gray-50 dark:hover:bg-gray-850 transition disabled:opacity-50"
								disabled={!!pendingAction || sending || threadBusy}
								onclick={() => runAction(kind)}
							>
								{#if pendingAction?.kind === kind}<Spinner className="size-3.5" />{/if}
								{labels()[kind]}
							</button>
						{/each}
					</div>

					<div
						class="flex items-center gap-1 border-b border-gray-100 dark:border-gray-850 mb-3"
						role="tablist"
					>
						{#each ['transcript', ...OUTPUT_KINDS] as value (value)}
							<button
								type="button"
								role="tab"
								aria-selected={tab === value}
								class="px-3 py-1.5 text-sm -mb-px border-b-2 transition {tab === value
									? 'border-gray-900 dark:border-gray-100 font-medium'
									: 'border-transparent text-gray-500'}"
								onclick={() => (tab = value as Tab)}
							>
								{tabLabel(value as Tab)}
							</button>
						{/each}
					</div>

					{#if tab === 'transcript'}
						<div class="flex flex-wrap items-center gap-1.5 mb-3">
							{#each speakers as speaker (speaker.label)}
								{#if editingSpeaker === speaker.label}
									<input
										class="px-2.5 py-1 text-xs rounded-full bg-gray-100 dark:bg-gray-850 outline-hidden w-36"
										aria-label={$i18n.t('Speaker name')}
										bind:value={speakerDraft}
										onkeydown={(event) => {
											if (event.key === 'Enter') saveSpeaker(speaker.label);
											if (event.key === 'Escape') editingSpeaker = null;
										}}
										onblur={() => saveSpeaker(speaker.label)}
									/>
								{:else}
									<Tooltip
										content={speaker.evidence
											? `${$i18n.t('Rename')} · “${speaker.evidence}”`
											: $i18n.t('Rename')}
									>
										<button
											type="button"
											class="px-2.5 py-1 text-xs rounded-full bg-gray-100 hover:bg-gray-200 dark:bg-gray-850 dark:hover:bg-gray-800 transition disabled:opacity-50"
											disabled={sending || threadBusy}
											onclick={async () => {
												speakerDraft = speaker.name ?? '';
												editingSpeaker = speaker.label;
											}}
										>
											{speaker.name || speaker.label}
										</button>
									</Tooltip>
								{/if}
							{/each}
							<label class="ml-auto flex items-center gap-1.5 text-xs text-gray-500 cursor-pointer">
								<input type="checkbox" bind:checked={showRaw} />
								{$i18n.t('Show original')}
							</label>
						</div>

						<div class="space-y-4">
							{#each turns as turn, index (index)}
								<div>
									<div class="flex items-baseline gap-2 text-xs text-gray-500 mb-0.5">
										<span class="font-medium text-gray-800 dark:text-gray-200"
											>{speakerName(speakers, turn.speaker)}</span
										>
										<span class="tabular-nums">{formatTimestamp(turn.start)}</span>
									</div>
									<div class="text-sm leading-relaxed dark:text-gray-200">
										{turn.texts.join(' ')}
									</div>
								</div>
							{:else}
								<div class="text-sm text-gray-500">{$i18n.t('No speech was recognised.')}</div>
							{/each}
						</div>
					{:else}
						{@const output = meeting.outputs?.[tab]}
						{#if pendingAction?.kind === tab}
							<div class="flex items-center gap-2 py-10 justify-center text-sm text-gray-500">
								<Spinner className="size-4" />
								{$i18n.t('Generating…')}
							</div>
						{:else if !output}
							<div class="py-10 text-center text-sm text-gray-500">
								{$i18n.t('Not generated yet. Use the button above.')}
							</div>
						{:else if tab === 'actions'}
							{@const items = meeting.outputs?.actions?.items ?? []}
							{#if items.length === 0}
								<div class="text-sm text-gray-500">{$i18n.t('No action items')}</div>
							{:else}
								<ul class="space-y-2">
									{#each items as item, index (index)}
										<li class="rounded-xl px-3 py-2 bg-gray-50 dark:bg-gray-850">
											<div class="text-sm dark:text-gray-100">{item.task}</div>
											<div class="text-xs text-gray-500 mt-0.5 flex flex-wrap gap-x-3">
												{#if item.owner}<span>{$i18n.t('Owner')}: {item.owner}</span>{/if}
												{#if item.due}<span>{$i18n.t('Due')}: {item.due}</span>{/if}
											</div>
											{#if item.quote}
												<div class="text-xs text-gray-500 italic mt-1">“{item.quote}”</div>
											{/if}
										</li>
									{/each}
								</ul>
							{/if}
						{:else}
							<div class="prose prose-sm dark:prose-invert max-w-none">
								<!-- eslint-disable-next-line svelte/no-at-html-tags -->
								{@html renderMarkdown((output as { markdown: string }).markdown ?? '')}
							</div>
						{/if}
					{/if}
				{:else if meeting.status === 'failed' && !meeting.error}
					<div class="py-10 text-center text-sm text-gray-500">
						{$i18n.t('This meeting could not be transcribed.')}
					</div>
				{/if}
			{/if}
		</div>
	</div>
</div>
