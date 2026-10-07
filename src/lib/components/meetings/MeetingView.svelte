<script lang="ts">
	// [Gradient] Vergadering: one meeting, rendered from the agent's latest meeting_state snapshot.
	// Header and subtitle follow the Notes editor; recording uses the dictation pill.
	import { getContext, onDestroy, onMount, tick, untrack } from 'svelte';
	import { fly } from 'svelte/transition';
	import { cubicOut } from 'svelte/easing';
	import type { Writable } from 'svelte/store';
	import type { i18n as i18nType } from 'i18next';
	import { toast } from 'svelte-sonner';

	import { beforeNavigate, goto, replaceState } from '$app/navigation';
	import dayjs from '$lib/dayjs';
	import { mobile, showSidebar } from '$lib/stores';
	import {
		MeetingApiError,
		deleteMeeting,
		getMeeting,
		sendMeetingInput,
		startMeeting,
		streamMeetingInput
	} from '$lib/apis/meetings';
	import Markdown from '$lib/components/chat/Messages/Markdown.svelte';
	import Skeleton from '$lib/components/chat/Messages/Skeleton.svelte';
	import { printDocument } from '$lib/utils/documentPrint';

	import ConfirmDialog from '$lib/components/common/ConfirmDialog.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import EllipsisHorizontal from '$lib/components/icons/EllipsisHorizontal.svelte';
	import ChatBubble from '$lib/components/icons/ChatBubble.svelte';
	import SidebarIcon from '$lib/components/icons/Sidebar.svelte';

	import ConsentForm from './ConsentForm.svelte';
	import DownloadButton from './DownloadButton.svelte';
	import MeetingMenu from './MeetingMenu.svelte';
	import MeetingRecorder from './MeetingRecorder.svelte';
	import { NoAudioTrackError, captureAudio, type AudioSource, type Capture } from './audio';
	import { markdownToDocx } from './docx';
	import { sendWhenIdle } from './recorder';
	import { reduceStream, startStream, type OutputStream } from './stream';
	import { attachesLeft, hasChanges, wordDiff, type DiffOp } from './diff';
	import { finishMeeting } from './finish';
	import { interruptedActions, nextChunkSeq, nextPart, partStore } from './parts';
	import {
		CONSENT_TEXT_VERSION,
		dayjsLocale,
		draftWithMeeting,
		REVEAL_DURATION_MS,
		revealDelays,
		revealTotalMs,
		OUTPUT_KINDS,
		fileStem,
		formatTimestamp,
		groupTurns,
		liveParts,
		meetingTimes,
		outputMarkdown,
		speakerName,
		speakerNames,
		transcriptMarkdown,
		wordCount,
		type ExportLabels,
		type MeetingInput,
		type MeetingMeta,
		type MeetingRead,
		type MeetingState,
		type OutputKind
	} from './meeting';

	const i18n: Writable<i18nType> = getContext('i18n');
	// Dates in the UI language, whatever the global dayjs locale is.
	const locale = $derived(dayjsLocale($i18n.languages, dayjs.Ls));
	const day = (value?: string | null) => dayjs(value ?? undefined).locale(locale);

	let { id }: { id: string } = $props();
	// The route remounts this view per id; `new` stays local after the shallow URL update.
	const initialId = untrack(() => id);

	type Tab = 'transcript' | OutputKind;
	type Pending = { kind: OutputKind; before: string; since: number; seen: boolean };

	let meetingId = $state<string | null>(initialId === 'new' ? null : initialId);
	let read = $state<MeetingRead | null>(null);
	let loading = $state(initialId !== 'new');
	let capture = $state<Capture | null>(null);
	let titleDraft = $state('');
	let titleFocused = $state(false);
	let titleSent = '';
	let titleTimer: ReturnType<typeof setTimeout> | null = null;
	let showDelete = $state(false);
	let showMenu = $state(false);
	let showChanges = $state(false);
	let tab = $state<Tab>('transcript');
	let pending = $state<Pending | null>(null);
	let outputStream = $state<OutputStream | null>(null);
	let streamAbort: AbortController | null = null;
	// Recovery of an interrupted meeting: its parts kept on this device, and the resumed recorder.
	let localParts = $state(0);
	let resuming = $state(false);
	let recovering = $state(false);
	let recorderPart = $state(1);
	let recorderSeq = $state(1);
	let resumedSession = $state(false);
	// A result that lands while the user watches is revealed item by item; opening one never animates.
	let reveal = $state<'transcript' | 'actions' | null>(null);
	let revealTimer: ReturnType<typeof setTimeout> | null = null;
	let followTimer: ReturnType<typeof setInterval> | null = null;
	let scroller: HTMLElement | null = $state(null);
	let sending = $state(false);
	// Set once this page sent `finish`; the agent's progress snapshot may lag behind its turn.
	let finishedHere = $state(false);
	let editingSpeaker = $state<string | null>(null);
	let speakerDraft = $state('');
	let pollTimer: ReturnType<typeof setTimeout> | null = null;
	let destroyed = false;

	const meeting: MeetingState | null = $derived(read?.state ?? null);
	const threadBusy = $derived(read?.status === 'running');
	const recording = $derived(capture !== null);
	const transcribing = $derived(
		meeting?.status === 'transcribing' ||
			(meeting?.status === 'recording' && !recording && (threadBusy || finishedHere))
	);
	const interrupted = $derived(
		meeting?.status === 'recording' && !recording && !threadBusy && !finishedHere && !recovering
	);
	const recoveryActions = $derived(
		interruptedActions({ localParts, liveParts: liveParts(meeting).length })
	);
	const activeAction = $derived<OutputKind | null>(
		(outputStream && !outputStream.final ? outputStream.kind : null) ??
			meeting?.pending_action ??
			pending?.kind ??
			null
	);
	const speakers = $derived(meeting?.transcript?.speakers ?? []);
	const turns = $derived(groupTurns(meeting?.transcript?.segments ?? []));
	const changed = $derived(hasChanges(meeting?.transcript?.segments ?? []));
	const live = $derived(liveParts(meeting));
	const tabHasContent = $derived(
		tab === 'transcript'
			? (meeting?.transcript?.segments?.length ?? 0) > 0
			: !!meeting?.outputs?.[tab]
	);
	const times = $derived(meetingTimes(meeting));
	const fallbackTitle = $derived(
		$i18n.t('Meeting of {{date}}', {
			date: day(times.startedAt ?? undefined).format('LL')
		})
	);
	const title = $derived(titleDraft.trim() || meeting?.title || fallbackTitle);

	$effect(() => {
		// Follow the stored title unless the user is typing or a save is still on its way.
		const stored = meeting?.title ?? '';
		untrack(() => {
			if (!titleFocused && !titleTimer && stored && stored !== titleDraft) {
				titleDraft = stored;
				titleSent = stored;
			}
		});
	});

	const startLabel = (iso: string) => {
		const start = day(iso);
		if (start.isSame(dayjs(), 'day')) return start.format($i18n.t('[Today at] h:mm A'));
		if (start.isSame(dayjs().subtract(1, 'day'), 'day'))
			return start.format($i18n.t('[Yesterday at] h:mm A'));
		return `${start.format($i18n.t('DD/MM/YYYY'))} ${start.format('LT')}`;
	};

	const subtitle = $derived.by(() => {
		const parts: string[] = [];
		const startedAt = times.startedAt ?? (meetingId ? null : new Date().toISOString());
		if (startedAt) {
			const end = times.endedAt ? `–${day(times.endedAt).format('LT')}` : '';
			parts.push(`${startLabel(startedAt)}${end}`);
		}
		if (times.durationS !== null) parts.push(formatTimestamp(times.durationS));
		if (speakers.length) parts.push($i18n.t('{{COUNT}} speakers', { COUNT: speakers.length }));
		if (meeting) parts.push($i18n.t('{{COUNT}} words', { COUNT: wordCount(meeting) }));
		return parts;
	});

	const labels = (): ExportLabels => ({
		transcript: $i18n.t('Transcript'),
		summary: $i18n.t('Summary'),
		minutes: $i18n.t('Minutes'),
		actions: $i18n.t('Action items'),
		owner: $i18n.t('Owner'),
		due: $i18n.t('Due'),
		noActions: $i18n.t('No action items'),
		date: $i18n.t('Date'),
		time: $i18n.t('Time'),
		duration: $i18n.t('Duration'),
		speakers: $i18n.t('Speakers')
	});

	const tabLabel = (value: Tab) =>
		value === 'transcript' ? $i18n.t('Transcript') : labels()[value];

	const outputKey = (snapshot: MeetingState | null, kind: OutputKind) =>
		JSON.stringify(snapshot?.outputs?.[kind] ?? null);

	const reducedMotion = () =>
		typeof window !== 'undefined' &&
		window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;

	const startReveal = (kind: 'transcript' | 'actions', count: number) => {
		if (reducedMotion() || count === 0) return;
		const atBottom =
			!!scroller && scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < 48;
		reveal = kind;
		if (revealTimer) clearTimeout(revealTimer);
		if (followTimer) clearInterval(followTimer);
		followTimer = atBottom
			? setInterval(
					() => scroller?.scrollTo({ top: scroller.scrollHeight, behavior: 'smooth' }),
					150
				)
			: null;
		revealTimer = setTimeout(
			() => {
				reveal = null;
				if (followTimer) clearInterval(followTimer);
				followTimer = null;
			},
			revealTotalMs(count) + 50
		);
	};

	const revealIn = (kind: 'transcript' | 'actions', index: number, count: number) =>
		reveal === kind
			? {
					y: 6,
					duration: REVEAL_DURATION_MS,
					delay: revealDelays(count)[index] ?? 0,
					easing: cubicOut
				}
			: { duration: 0 };

	const refreshLocalParts = async () => {
		if (meetingId) localParts = (await partStore().parts(meetingId)).length;
	};

	const load = async () => {
		if (!meetingId) return;
		const wasTranscribing = transcribing;
		const actionsBefore = pending?.kind === 'actions' ? pending.before : null;
		try {
			read = await getMeeting(localStorage.token, meetingId);
			if (meeting?.status === 'recording' && !capture) await refreshLocalParts();
			if (wasTranscribing && meeting?.status === 'ready') {
				tab = 'transcript';
				startReveal('transcript', groupTurns(meeting.transcript?.segments ?? []).length);
			}
			if (actionsBefore !== null && outputKey(meeting, 'actions') !== actionsBefore) {
				startReveal('actions', meeting?.outputs?.actions?.items?.length ?? 0);
			}
		} catch (error) {
			if ((error as { status?: number })?.status === 404) {
				toast.error($i18n.t('This meeting no longer exists.'));
				goto('/meetings');
				return;
			}
			console.error(error);
		} finally {
			loading = false;
		}
		if (pending) {
			if (meeting?.pending_action) {
				pending.seen = true;
			} else {
				const changed = outputKey(meeting, pending.kind) !== pending.before;
				const failed = meeting?.error?.stage === 'action';
				const stale = Date.now() - pending.since > 10 * 60_000;
				if (!threadBusy && (pending.seen || changed || failed || stale)) pending = null;
			}
		}
	};

	const pollDelay = (): number | null => {
		if (recording) return 5000;
		if (sending || pending || activeAction || threadBusy || transcribing) return 2000;
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

	const send = async (input: MeetingInput, { quiet = false } = {}) => {
		if (!meetingId) return false;
		sending = true;
		try {
			await sendWhenIdle(() => sendMeetingInput(localStorage.token, meetingId as string, input));
			return true;
		} catch (error) {
			if (!quiet) toast.error(`${(error as Error)?.message ?? error}`);
			return false;
		} finally {
			sending = false;
			await load();
			schedule();
		}
	};

	const saveTitle = async () => {
		if (titleTimer) clearTimeout(titleTimer);
		titleTimer = null;
		const value = titleDraft.trim();
		if (!meetingId || !value || value === titleSent) return;
		titleSent = value;
		await send({ type: 'set_title', title: value });
	};

	const onTitleInput = () => {
		if (!meetingId) return;
		if (titleTimer) clearTimeout(titleTimer);
		titleTimer = setTimeout(saveTitle, 800);
	};

	const start = async (source: AudioSource) => {
		let captured: Capture;
		try {
			// First call in the click: the share picker needs the user gesture.
			captured = await captureAudio(source);
		} catch (error) {
			console.error(error);
			toast.error(
				error instanceof NoAudioTrackError
					? $i18n.t('No audio was shared. Choose a browser tab and turn on "Share tab audio".')
					: $i18n.t('Error accessing media devices.')
			);
			return;
		}
		try {
			const chosen = titleDraft.trim();
			const res = await startMeeting(localStorage.token, {
				type: 'start',
				...(chosen ? { title: chosen } : {}),
				consent: { text_version: CONSENT_TEXT_VERSION, at: new Date().toISOString() }
			});
			titleSent = chosen;
			meetingId = res.id;
			recorderPart = 1;
			recorderSeq = 1;
			resumedSession = false;
			capture = captured;
			replaceState(`/meetings/${encodeURIComponent(res.id)}`, {});
			await load();
			schedule();
		} catch (error) {
			captured.release();
			toast.error(`${(error as Error)?.message ?? error}`);
		}
	};

	const recorded = async () => {
		finishedHere = true;
		capture = null;
		await load();
		schedule();
	};

	const discarded = async () => {
		capture = null;
		if (resumedSession) {
			// A discarded resume leaves the meeting as it was: interrupted, still recoverable.
			await load();
			return;
		}
		if (meetingId) await deleteMeeting(localStorage.token, meetingId).catch(() => {});
		goto('/meetings');
	};

	/** Runs an action on the turn's own stream so the output writes itself into its tab (C6). */
	const runAction = async (kind: OutputKind) => {
		if (!meetingId) return;
		tab = kind;
		const before = outputKey(meeting, kind);
		let stream = startStream(kind);
		outputStream = stream;
		const controller = new AbortController();
		streamAbort = controller;
		try {
			await sendWhenIdle(() =>
				streamMeetingInput(
					localStorage.token,
					meetingId as string,
					{ type: 'action', kind, template_id: null },
					(frame) => {
						stream = reduceStream(stream, frame);
						outputStream = stream;
						if (stream.final && read) {
							read = { ...read, state: stream.final };
							if (kind === 'actions' && outputKey(stream.final, 'actions') !== before) {
								startReveal('actions', stream.final.outputs?.actions?.items?.length ?? 0);
							}
						}
					},
					controller.signal
				)
			);
		} catch (error) {
			if (controller.signal.aborted) return;
			if (error instanceof MeetingApiError) {
				toast.error(error.message);
				outputStream = null;
				return;
			}
			console.error('Output stream dropped', error);
		} finally {
			if (streamAbort === controller) streamAbort = null;
		}
		if (!stream.final) {
			// The stream dropped before the result: the turn runs on, so poll for it as before.
			pending = { kind, before, since: Date.now(), seen: false };
		}
		outputStream = null;
		await load();
		schedule();
	};

	const saveSpeaker = async (label: string) => {
		const name = speakerDraft.trim();
		editingSpeaker = null;
		if (!name || name === speakerName(speakers, label)) return;
		await send({ type: 'rename_speaker', label, name });
	};

	/** Continue an interrupted meeting as a new part; its consent stands. */
	const resume = async (source: AudioSource) => {
		let captured: Capture;
		try {
			captured = await captureAudio(source);
		} catch (error) {
			console.error(error);
			toast.error(
				error instanceof NoAudioTrackError
					? $i18n.t('No audio was shared. Choose a browser tab and turn on "Share tab audio".')
					: $i18n.t('Error accessing media devices.')
			);
			return;
		}
		recorderPart = nextPart(await partStore().parts(meetingId as string));
		recorderSeq = nextChunkSeq(meeting?.live);
		resuming = false;
		resumedSession = true;
		capture = captured;
	};

	/** Finish from the parts on this device, or with none: then only the live transcript is used. */
	const finishInterrupted = async (useLocalParts: boolean) => {
		if (!meetingId) return;
		recovering = true;
		try {
			const parts = useLocalParts ? await partStore().parts(meetingId) : [];
			await finishMeeting(
				localStorage.token,
				meetingId,
				parts.map(({ blob }) => blob)
			);
			finishedHere = true;
		} catch (error) {
			toast.error(`${(error as Error)?.message ?? error}`);
		} finally {
			recovering = false;
			await load();
			schedule();
		}
	};

	/** A new chat with this meeting attached, through the draft the new chat restores. */
	const chatAbout = () => {
		if (!meetingId) return;
		const item = {
			type: 'meeting' as const,
			id: meetingId,
			name: title,
			status: 'processed' as const
		};
		try {
			sessionStorage.setItem(
				'chat-input',
				draftWithMeeting(sessionStorage.getItem('chat-input'), item)
			);
		} catch (error) {
			console.error(error);
		}
		goto('/');
	};

	const remove = async () => {
		if (!meetingId) return;
		try {
			await deleteMeeting(localStorage.token, meetingId);
			await partStore().remove(meetingId);
			toast.success($i18n.t('Meeting deleted'));
			goto('/meetings');
		} catch (error) {
			toast.error(`${(error as Error)?.message ?? error}`);
		}
	};

	const meta = (): MeetingMeta => ({
		title,
		date: times.startedAt ? day(times.startedAt).format('LL') : null,
		time: times.startedAt
			? `${day(times.startedAt).format('LT')}${times.endedAt ? `–${day(times.endedAt).format('LT')}` : ''}`
			: null,
		duration: times.durationS !== null ? formatTimestamp(times.durationS) : null,
		speakers: speakerNames(meeting)
	});

	const exportMarkdown = (): string | null => {
		if (!meeting) return null;
		return tab === 'transcript'
			? transcriptMarkdown(meta(), meeting, labels())
			: outputMarkdown(meta(), meeting, tab, labels());
	};

	const saveAs = (blob: Blob, name: string) => {
		const url = URL.createObjectURL(blob);
		const link = Object.assign(document.createElement('a'), { href: url, download: name });
		document.body.append(link);
		link.click();
		link.remove();
		setTimeout(() => URL.revokeObjectURL(url), 1000);
	};

	const download = async (format: 'md' | 'docx' | 'pdf') => {
		const markdown = exportMarkdown();
		if (!markdown) {
			toast.error($i18n.t('Not generated yet.'));
			return;
		}
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

	beforeNavigate(({ cancel, type }) => {
		if (recording && type !== 'leave' && !confirm($i18n.t('Stop this recording and leave?')))
			cancel();
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
		streamAbort?.abort();
		if (revealTimer) clearTimeout(revealTimer);
		if (followTimer) clearInterval(followTimer);
		if (titleTimer) saveTitle();
	});

	$effect(() => {
		// Re-arm polling whenever the snapshot or local activity changes what we wait for.
		void [read, pending, sending, capture];
		tick().then(schedule);
	});

	const tabClass = (selected: boolean) =>
		`min-w-fit p-1.5 ${selected ? '' : 'text-gray-300 dark:text-gray-600 hover:text-gray-700 dark:hover:text-white'} transition`;
	const secondaryButton =
		'flex items-center gap-1.5 rounded-lg bg-gray-50 px-2.5 py-1 text-xs text-gray-900 transition ring-1 ring-gray-200 hover:bg-gray-100 dark:bg-gray-850 dark:text-gray-100 dark:ring-gray-800 dark:hover:bg-gray-800 disabled:opacity-50 disabled:cursor-not-allowed';
</script>

<ConfirmDialog
	bind:show={showDelete}
	title={$i18n.t('Delete meeting?')}
	message={$i18n.t(
		'The transcript and all outputs of this meeting are deleted. This cannot be undone.'
	)}
	confirmLabel={$i18n.t('Delete')}
	onConfirm={remove}
/>

{#snippet liveTranscript()}
	<!-- The list stays mounted so only paragraphs that arrive later fade in (local transitions). -->
	<div class={live.length > 0 ? 'mt-4' : ''}>
		{#if live.length > 0}
			<div class="mb-2 text-xs text-gray-500">{$i18n.t('Live transcript (rough)')}</div>
		{/if}
		<div class="space-y-3">
			{#each live as part, index (index)}
				<p
					class="text-sm leading-relaxed text-gray-700 dark:text-gray-300"
					in:fly={reducedMotion()
						? { duration: 0 }
						: { y: 6, duration: REVEAL_DURATION_MS, easing: cubicOut }}
				>
					{part}
				</p>
			{/each}
		</div>
	</div>
{/snippet}

{#snippet diffText(ops: DiffOp[])}
	{#each ops as op, index (index)}{#if index > 0 && !attachesLeft(op.text)}{' '}{/if}{#if op.type === 'same'}{op.text}{:else if op.type === 'removed'}<del
				class="text-gray-400 dark:text-gray-500 decoration-gray-400/70 {op.punctuation
					? 'opacity-60'
					: ''}">{op.text}</del
			>{:else}<ins
				class="no-underline rounded-sm bg-gray-100 px-0.5 dark:bg-gray-800 {op.punctuation
					? 'opacity-60'
					: ''}">{op.text}</ins
			>{/if}{/each}
{/snippet}

<div
	class="flex flex-col w-full h-screen max-h-[100dvh] transition-width duration-200 ease-in-out {$showSidebar
		? 'md:max-w-[calc(100%-var(--sidebar-width))]'
		: ''} max-w-full"
>
	<div
		bind:this={scroller}
		class="relative flex-1 w-full max-h-full overflow-y-auto flex justify-center pt-2"
	>
		<div class="w-full flex flex-col">
			<div class="shrink-0 w-full flex justify-between items-center px-3">
				<div class="w-full min-w-0 flex items-center">
					{#if $mobile}
						<Tooltip content={$showSidebar ? $i18n.t('Close Sidebar') : $i18n.t('Open Sidebar')}>
							<button
								class=" cursor-pointer flex rounded-lg hover:bg-gray-100 dark:hover:bg-gray-850 transition"
								aria-label={$showSidebar ? $i18n.t('Close Sidebar') : $i18n.t('Open Sidebar')}
								type="button"
								onclick={() => showSidebar.set(!$showSidebar)}
							>
								<div class=" self-center p-1.5">
									<SidebarIcon className="size-4" />
								</div>
							</button>
						</Tooltip>
					{/if}

					<input
						class="w-full text-sm font-normal bg-transparent outline-hidden {$mobile ? 'ml-1' : ''}"
						type="text"
						maxlength="200"
						aria-label={$i18n.t('Title')}
						placeholder={fallbackTitle}
						bind:value={titleDraft}
						oninput={onTitleInput}
						onfocus={() => (titleFocused = true)}
						onblur={() => {
							titleFocused = false;
							saveTitle();
						}}
						onkeydown={(event) => {
							if (event.key === 'Enter') (event.currentTarget as HTMLInputElement).blur();
						}}
					/>
				</div>

				{#if meetingId && !recording}
					<div class="flex items-center gap-0.5 shrink-0">
						{#if meeting?.status === 'ready'}
							<Tooltip content={$i18n.t('Chat about this meeting')}>
								<button
									class="self-center p-1 hover:bg-black/5 dark:hover:bg-white/5 rounded-md transition"
									type="button"
									aria-label={$i18n.t('Chat about this meeting')}
									onclick={chatAbout}
								>
									<ChatBubble className="size-4" />
								</button>
							</Tooltip>
						{/if}
						<MeetingMenu
							bind:show={showMenu}
							onDownload={meeting?.status === 'ready' ? download : null}
							downloadLabel={tabLabel(tab)}
							onChat={meeting?.status === 'ready' ? chatAbout : null}
							onDelete={() => (showDelete = true)}
						>
							<button
								class="self-center p-1 hover:bg-black/5 dark:hover:bg-white/5 rounded-md transition"
								type="button"
								aria-label={$i18n.t('Meeting menu')}
							>
								<EllipsisHorizontal className="size-4" />
							</button>
						</MeetingMenu>
					</div>
				{/if}
			</div>

			<div class="px-1.5">
				<div class="flex w-full bg-transparent overflow-x-auto scrollbar-none">
					<div
						class="flex gap-0.5 items-center text-xs font-normal text-gray-500 dark:text-gray-500 w-fit"
					>
						{#each subtitle as part, index (index)}
							<span class="py-1 px-1.5 min-w-fit">{part}</span>
						{/each}
					</div>
				</div>
			</div>

			<div class="flex-1 w-full px-3 pt-3 pb-10">
				{#if !meetingId}
					<ConsentForm onStart={start} onCancel={() => goto('/meetings')} />
				{:else if resuming && !capture}
					<ConsentForm resume onStart={resume} onCancel={() => (resuming = false)} />
					<div class="px-2 text-xs leading-5 text-gray-500">
						{$i18n.t(
							'Speakers in the new part continue as new speakers (e.g. Speaker 3); rename them to merge them with earlier ones.'
						)}
					</div>
				{:else if capture}
					<MeetingRecorder
						{meetingId}
						{capture}
						part={recorderPart}
						firstSeq={recorderSeq}
						onFinished={recorded}
						onDiscard={discarded}
					/>
					{#if live.length === 0}
						<div class="mt-4 text-xs text-gray-500">
							{$i18n.t('The first part appears after about half a minute.')}
						</div>
					{/if}
					{@render liveTranscript()}
				{:else if loading}
					<div class="flex justify-center py-10"><Spinner className="size-4" /></div>
				{:else}
					{#if meeting?.error && !transcribing}
						<div
							class="mb-3 flex items-center gap-2 rounded-xl bg-gray-50 px-3 py-2 text-xs text-gray-700 dark:bg-gray-850 dark:text-gray-300"
							role="alert"
						>
							<div class="flex-1">{meeting.error.message}</div>
							{#if meeting.error.retryable}
								<button
									type="button"
									class={secondaryButton}
									disabled={sending || threadBusy}
									onclick={() => send({ type: 'retry' })}
								>
									{$i18n.t('Try again')}
								</button>
							{/if}
						</div>
					{/if}

					{#if recovering}
						<div class="flex items-center gap-2 text-xs text-gray-500">
							<Spinner className="size-3.5" />
							{$i18n.t('Uploading recording…')}
						</div>
						{@render liveTranscript()}
					{:else if interrupted}
						<div class="text-sm text-gray-800 dark:text-gray-200">
							{$i18n.t('This recording was interrupted.')}
						</div>
						<div class="mt-2 flex flex-wrap items-center gap-2">
							{#if recoveryActions.includes('finish_local')}
								<button
									type="button"
									class={secondaryButton}
									onclick={() => finishInterrupted(true)}
								>
									{$i18n.t('Finish recording')}
								</button>
							{/if}
							<button type="button" class={secondaryButton} onclick={() => (resuming = true)}>
								{$i18n.t('Continue recording')}
							</button>
							{#if recoveryActions.includes('finish_live')}
								<button
									type="button"
									class={secondaryButton}
									onclick={() => finishInterrupted(false)}
								>
									{$i18n.t('Finish with the live transcript')}
								</button>
							{/if}
						</div>
						<div class="mt-2 text-xs leading-5 text-gray-500">
							{#if recoveryActions.includes('finish_local')}
								{$i18n.t('The recording is still on this device and can be finished from here.')}
							{/if}
							{#if recoveryActions.includes('finish_live')}
								{$i18n.t(
									'Finishing with the live transcript uses the rough text only, without speakers.'
								)}
							{/if}
						</div>
						{@render liveTranscript()}
					{:else if !meeting || transcribing}
						<div class="flex items-center gap-2 text-xs text-gray-500">
							<Spinner className="size-3.5" />
							<span class="text-gray-700 dark:text-gray-300">{$i18n.t('Transcribing…')}</span>
							<span
								>{$i18n.t(
									'The transcript is being cleaned up and speakers recognised. This can take a few minutes.'
								)}</span
							>
						</div>
						{@render liveTranscript()}
					{:else if meeting.status === 'ready'}
						<div class="flex flex-wrap items-center justify-between gap-2 mb-2">
							<div
								class="flex gap-1 scrollbar-none overflow-x-auto w-fit text-center text-sm font-medium rounded-full bg-transparent"
								role="tablist"
							>
								{#each ['transcript', ...OUTPUT_KINDS] as value (value)}
									<button
										type="button"
										role="tab"
										aria-selected={tab === value}
										class="{tabClass(tab === value)} flex items-center gap-1"
										onclick={() => (tab = value as Tab)}
									>
										{tabLabel(value as Tab)}
									</button>
								{/each}
							</div>

							<div class="flex items-center gap-2">
								{#if tab === 'transcript'}
									<label
										class="flex items-center gap-1.5 text-xs text-gray-500 {changed
											? 'cursor-pointer'
											: 'opacity-70'}"
									>
										<input type="checkbox" bind:checked={showChanges} disabled={!changed} />
										{changed ? $i18n.t('Show changes') : $i18n.t('No changes')}
									</label>
								{:else}
									<button
										type="button"
										class={secondaryButton}
										disabled={!!activeAction || sending || threadBusy}
										onclick={() => runAction(tab as OutputKind)}
									>
										{meeting.outputs?.[tab as OutputKind]
											? $i18n.t('Regenerate')
											: $i18n.t('Generate {{name}}', { name: tabLabel(tab).toLowerCase() })}
									</button>
								{/if}
								{#if tabHasContent && activeAction !== tab}
									<DownloadButton className={secondaryButton} onDownload={download} />
								{/if}
							</div>
						</div>

						{#if tab === 'transcript'}
							{#if speakers.length}
								<div class="flex flex-wrap items-center gap-1 mb-3">
									<span class="text-xs text-gray-500 mr-1">{$i18n.t('Speakers')}</span>
									{#each speakers as speaker (speaker.label)}
										{#if editingSpeaker === speaker.label}
											<!-- svelte-ignore a11y_autofocus -->
											<input
												class="px-2 py-0.5 text-xs rounded-lg bg-gray-50 dark:bg-gray-850 ring-1 ring-gray-200 dark:ring-gray-800 outline-hidden w-36"
												aria-label={$i18n.t('Speaker name')}
												autofocus
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
													class="px-2 py-0.5 text-xs rounded-lg bg-gray-50 hover:bg-gray-100 dark:bg-gray-850 dark:hover:bg-gray-800 ring-1 ring-gray-200 dark:ring-gray-800 transition disabled:opacity-50"
													disabled={sending || threadBusy}
													onclick={() => {
														speakerDraft = speaker.name ?? '';
														editingSpeaker = speaker.label;
													}}
												>
													{speaker.name || speaker.label}
												</button>
											</Tooltip>
										{/if}
									{/each}
								</div>
							{/if}

							<div class="space-y-4">
								{#each turns as turn, index (index)}
									<div in:fly|global={revealIn('transcript', index, turns.length)}>
										<div class="flex items-baseline gap-2 text-xs text-gray-500 mb-0.5">
											<span class="font-medium text-gray-800 dark:text-gray-200"
												>{speakerName(speakers, turn.speaker)}</span
											>
											<span class="tabular-nums">{formatTimestamp(turn.start)}</span>
										</div>
										<div class="text-sm leading-relaxed text-gray-800 dark:text-gray-200">
											{#if showChanges && changed}
												{@render diffText(
													turn.segments.flatMap((segment) =>
														wordDiff(segment.raw, segment.clean || segment.raw)
													)
												)}
											{:else}
												{turn.texts.join(' ')}
											{/if}
										</div>
									</div>
								{:else}
									<div class="text-xs text-gray-500">{$i18n.t('No speech was recognised.')}</div>
								{/each}
							</div>
						{:else}
							{@const output = meeting.outputs?.[tab]}
							{#if outputStream && outputStream.kind === tab && !outputStream.final && outputStream.text && tab !== 'actions'}
								<div class="markdown-prose">
									<Markdown
										id={`meeting-${meetingId}-${tab}-stream`}
										content={outputStream.text}
										done={false}
									/>
								</div>
								<div class="text-[0.9375rem] leading-relaxed">
									<span
										class="inline-block w-[0.125rem] h-3.5 bg-gray-400 dark:bg-gray-500 ml-0.5 animate-pulse align-text-bottom"
									></span>
								</div>
							{:else if activeAction === tab}
								<Skeleton />
							{:else if !output}
								<div class="flex w-full flex-col items-center justify-center py-16">
									<div class="max-w-sm text-center text-gray-900 dark:text-gray-100">
										<div class="mb-1.5 text-sm">{$i18n.t('Not generated yet.')}</div>
										<div class="text-xs leading-5 text-gray-500">
											{$i18n.t('Use the button above to make it from the transcript.')}
										</div>
									</div>
								</div>
							{:else if tab === 'actions'}
								{@const items = meeting.outputs?.actions?.items ?? []}
								{#if items.length === 0}
									<div class="text-xs text-gray-500">{$i18n.t('No action items')}</div>
								{:else}
									<ul class="flex flex-col gap-y-0.5">
										{#each items as item, index (index)}
											<li
												class="rounded-xl px-2 py-1.5 hover:bg-gray-50 dark:hover:bg-gray-900"
												in:fly|global={revealIn('actions', index, items.length)}
											>
												<div class="text-sm text-gray-800 dark:text-gray-200">{item.task}</div>
												<div class="text-xs text-gray-500 flex flex-wrap gap-x-3">
													{#if item.owner}<span>{$i18n.t('Owner')}: {item.owner}</span>{/if}
													{#if item.due}<span>{$i18n.t('Due')}: {item.due}</span>{/if}
													{#if item.quote}<span class="italic">“{item.quote}”</span>{/if}
												</div>
											</li>
										{/each}
									</ul>
								{/if}
							{:else}
								<div class="markdown-prose">
									<Markdown
										id={`meeting-${meetingId}-${tab}`}
										content={(output as { markdown: string }).markdown ?? ''}
									/>
								</div>
							{/if}
						{/if}
					{:else if meeting.status === 'failed' && !meeting.error}
						<div class="text-xs text-gray-500">
							{$i18n.t('This meeting could not be transcribed.')}
						</div>
						{@render liveTranscript()}
					{:else if meeting.status === 'failed'}
						{@render liveTranscript()}
					{/if}
				{/if}
			</div>
		</div>
	</div>
</div>
