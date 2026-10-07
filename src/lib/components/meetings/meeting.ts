// [Gradient] Vergadering: the meeting agent's contract (C3/C4) and pure helpers over its snapshot.

export const CONSENT_TEXT_VERSION = '2026-10-07';
export const MEETING_AGENT = 'meeting';

export type MeetingStatus = 'recording' | 'transcribing' | 'ready' | 'failed';
export type OutputKind = 'summary' | 'minutes' | 'actions';
export const OUTPUT_KINDS: OutputKind[] = ['summary', 'minutes', 'actions'];

export type AudioRef = {
	id: string;
	sha256?: string;
	size?: number;
	media_type?: string;
	name?: string;
};

export type MeetingInput =
	| {
			type: 'start';
			title?: string;
			language?: string;
			consent: { text_version: string; at: string };
	  }
	| { type: 'chunk'; seq: number; audio_ref: AudioRef }
	| { type: 'finish'; audio_refs: AudioRef[] }
	| { type: 'retry' }
	| { type: 'rename_speaker'; label: string; name: string }
	| { type: 'set_title'; title: string }
	| { type: 'action'; kind: OutputKind; template_id: string | null };

export type Speaker = {
	label: string;
	name: string | null;
	evidence?: string | null;
	inferred?: boolean;
};

export type Segment = {
	start: number;
	end: number;
	speaker: string;
	raw: string;
	clean: string;
};

export type ActionItem = {
	task: string;
	owner: string | null;
	due: string | null;
	quote?: string | null;
};

export type MeetingState = {
	status: MeetingStatus;
	title?: string | null;
	language?: string | null;
	consent?: { text_version: string; at: string } | null;
	live?: { seq: number; text: string }[];
	transcript: null | { speakers: Speaker[]; segments: Segment[] };
	outputs?: {
		summary?: null | { markdown: string };
		minutes?: null | { markdown: string };
		actions?: null | { items: ActionItem[] };
	};
	error?: null | { stage: 'chunk' | 'finish' | 'action'; message: string; retryable: boolean };
	audio_retained?: boolean;
	usage?: { audio_seconds?: number; stt_output_tokens?: number };
	pending_action?: OutputKind | null;
	started_at?: string | null;
	ended_at?: string | null;
	duration_s?: number | null;
};

export type MeetingRead = { status: string; state: MeetingState | null };
export type MeetingSummary = {
	thread_id: string;
	agent: string;
	created_at: string;
	title: string | null;
};
export type MeetingAgent = {
	name: string;
	kind: 'chat' | 'surface';
	title?: string;
	description?: string;
	input_version?: string;
};

export const hasMeetingAgent = (agents: MeetingAgent[] | undefined | null): boolean =>
	(agents ?? []).some((agent) => agent?.name === MEETING_AGENT);

export const formatTimestamp = (seconds: number): string => {
	const total = Math.max(0, Math.floor(seconds || 0));
	const h = Math.floor(total / 3600);
	const m = Math.floor((total % 3600) / 60);
	const s = total % 60;
	const pad = (n: number) => String(n).padStart(2, '0');
	return h > 0 ? `${h}:${pad(m)}:${pad(s)}` : `${pad(m)}:${pad(s)}`;
};

export const speakerName = (speakers: Speaker[], label: string): string =>
	speakers.find((speaker) => speaker.label === label)?.name || label;

export type SpeakerTurn = {
	speaker: string;
	start: number;
	end: number;
	texts: string[];
	segments: Segment[];
};

/** Consecutive segments of one speaker form one turn; order is kept. */
export const groupTurns = (segments: Segment[], raw = false): SpeakerTurn[] => {
	const turns: SpeakerTurn[] = [];
	for (const segment of segments) {
		const text = (raw ? segment.raw : segment.clean || segment.raw) ?? '';
		const last = turns.at(-1);
		if (last && last.speaker === segment.speaker) {
			last.texts.push(text);
			last.segments.push(segment);
			last.end = segment.end;
		} else {
			turns.push({
				speaker: segment.speaker,
				start: segment.start,
				end: segment.end,
				texts: [text],
				segments: [segment]
			});
		}
	}
	return turns;
};

/** Live parts in sequence order, one paragraph each. */
export const liveParts = (state: MeetingState | null): string[] =>
	[...(state?.live ?? [])]
		.sort((a, b) => a.seq - b.seq)
		.map((part) => part.text.trim())
		.filter(Boolean);

export type MeetingTimes = {
	startedAt: string | null;
	endedAt: string | null;
	durationS: number | null;
};

/** Start, end and duration; meetings from before `started_at` fall back to the consent time. */
export const meetingTimes = (state: MeetingState | null): MeetingTimes => {
	const startedAt = state?.started_at ?? state?.consent?.at ?? null;
	const endedAt = state?.ended_at ?? null;
	let durationS = state?.duration_s ?? null;
	if (durationS === null && startedAt && endedAt) {
		const ms = Date.parse(endedAt) - Date.parse(startedAt);
		durationS = Number.isFinite(ms) && ms >= 0 ? Math.round(ms / 1000) : null;
	}
	return { startedAt, endedAt, durationS };
};

const countWords = (text: string) => text.split(/\s+/).filter(Boolean).length;

/** Words in the clean transcript once it exists, else in the live parts. */
export const wordCount = (state: MeetingState | null): number => {
	const segments = state?.transcript?.segments ?? [];
	if (segments.length > 0) {
		return segments.reduce(
			(sum, segment) => sum + countWords(segment.clean || segment.raw || ''),
			0
		);
	}
	return liveParts(state).reduce((sum, part) => sum + countWords(part), 0);
};

export type ExportLabels = {
	transcript: string;
	summary: string;
	minutes: string;
	actions: string;
	owner: string;
	due: string;
	noActions: string;
	date: string;
	time: string;
	duration: string;
	speakers: string;
};

/** Already-formatted metadata (locale and timezone are the caller's). */
export type MeetingMeta = {
	title: string;
	date: string | null;
	time: string | null;
	duration: string | null;
	speakers: string[];
};

export const speakerNames = (state: MeetingState | null): string[] =>
	(state?.transcript?.speakers ?? []).map((speaker) => speaker.name || speaker.label);

/** The header every download starts with: title, date, start–end time, duration and speakers. */
export const metadataMarkdown = (meta: MeetingMeta, labels: ExportLabels): string => {
	const rows: [string, string | null][] = [
		[labels.date, meta.date],
		[labels.time, meta.time],
		[labels.duration, meta.duration],
		[labels.speakers, meta.speakers.length ? meta.speakers.join(', ') : null]
	];
	const lines = rows.filter(([, value]) => value).map(([label, value]) => `**${label}:** ${value}`);
	return [`# ${meta.title}`, '', ...lines.flatMap((line) => [line, ''])].join('\n');
};

export const transcriptMarkdown = (
	meta: MeetingMeta,
	state: MeetingState,
	labels: ExportLabels,
	raw = false
): string => {
	const transcript = state.transcript;
	const lines = [metadataMarkdown(meta, labels), `## ${labels.transcript}`, ''];
	for (const turn of groupTurns(transcript?.segments ?? [], raw)) {
		lines.push(
			`**${speakerName(transcript?.speakers ?? [], turn.speaker)}** (${formatTimestamp(turn.start)})`,
			'',
			turn.texts.join(' ').trim(),
			''
		);
	}
	return lines.join('\n').trimEnd() + '\n';
};

export const actionsMarkdown = (items: ActionItem[], labels: ExportLabels): string => {
	if (items.length === 0) return `${labels.noActions}\n`;
	return (
		items
			.map((item) => {
				const meta = [
					item.owner ? `${labels.owner}: ${item.owner}` : null,
					item.due ? `${labels.due}: ${item.due}` : null
				].filter(Boolean);
				return `- ${item.task}${meta.length ? ` (${meta.join(', ')})` : ''}`;
			})
			.join('\n') + '\n'
	);
};

export const outputMarkdown = (
	meta: MeetingMeta,
	state: MeetingState,
	kind: OutputKind,
	labels: ExportLabels
): string | null => {
	const output = state.outputs?.[kind];
	if (!output) return null;
	const body =
		kind === 'actions'
			? actionsMarkdown((output as { items: ActionItem[] }).items ?? [], labels)
			: ((output as { markdown: string }).markdown ?? '');
	return `${metadataMarkdown(meta, labels)}## ${labels[kind]}\n\n${body.trim()}\n`;
};

/** Safe file name stem: keeps letters (incl. accents), digits, spaces, dots and dashes. */
export const fileStem = (value: string): string =>
	value
		.replace(/[^\p{L}\p{N} ._-]+/gu, '')
		.trim()
		.replace(/\s+/g, '-')
		.slice(0, 80) || 'vergadering';

/** Client-side title search, case- and accent-insensitive. */
export const matchesQuery = (title: string | null | undefined, query: string): boolean => {
	const fold = (value: string) => value.normalize('NFD').replace(/\p{M}/gu, '').toLowerCase();
	const needle = fold(query.trim());
	return !needle || fold(title ?? '').includes(needle);
};

/** Groups items (already newest first) under the label `rangeOf` gives each, keeping order. */
export const groupByRange = <T>(items: T[], rangeOf: (item: T) => string): [string, T[]][] => {
	const groups = new Map<string, T[]>();
	for (const item of items) {
		const range = rangeOf(item);
		groups.set(range, [...(groups.get(range) ?? []), item]);
	}
	return [...groups.entries()];
};

/** The dayjs locale for the UI languages (e.g. ['nl-NL', 'nl', 'en']), from those dayjs has loaded. */
export const dayjsLocale = (languages: readonly string[] | undefined, loaded: object): string => {
	for (const language of languages ?? []) {
		const code = language.toLowerCase();
		if (code in loaded) return code;
		const base = code.split('-')[0];
		if (base in loaded) return base;
	}
	return 'en';
};

export const REVEAL_STEP_MS = 70;
export const REVEAL_CAP_MS = 3000;
export const REVEAL_DURATION_MS = 250;

/**
 * Start delay of each item when a finished transcript or list is revealed: one step apart,
 * and everything after the cap appears together, so a long meeting is shown within ~3 s.
 */
export const revealDelays = (
	count: number,
	{ stepMs = REVEAL_STEP_MS, capMs = REVEAL_CAP_MS } = {}
): number[] =>
	Array.from({ length: Math.max(0, count) }, (_, index) =>
		Math.min(index * stepMs, Math.max(0, capMs - REVEAL_DURATION_MS))
	);

/** How long a reveal of `count` items takes from its start to the last item settling. */
export const revealTotalMs = (count: number, options?: { stepMs?: number; capMs?: number }) =>
	(revealDelays(count, options).at(-1) ?? 0) + REVEAL_DURATION_MS;
