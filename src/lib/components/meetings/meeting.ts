// [Gradient] Vergadering: the meeting agent's contract (C3/C4) and pure helpers over its snapshot.

export const CONSENT_TEXT_VERSION = '2026-10-07';
export const MEETING_AGENT = 'meeting';

export type AudioSource = 'microphone' | 'display';
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
	| { type: 'finish'; audio_ref: AudioRef }
	| { type: 'retry' }
	| { type: 'rename_speaker'; label: string; name: string }
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

export type SpeakerTurn = { speaker: string; start: number; end: number; texts: string[] };

/** Consecutive segments of one speaker form one turn; order is kept. */
export const groupTurns = (segments: Segment[], raw = false): SpeakerTurn[] => {
	const turns: SpeakerTurn[] = [];
	for (const segment of segments) {
		const text = (raw ? segment.raw : segment.clean || segment.raw) ?? '';
		const last = turns.at(-1);
		if (last && last.speaker === segment.speaker) {
			last.texts.push(text);
			last.end = segment.end;
		} else {
			turns.push({
				speaker: segment.speaker,
				start: segment.start,
				end: segment.end,
				texts: [text]
			});
		}
	}
	return turns;
};

export const liveText = (state: MeetingState | null): string =>
	[...(state?.live ?? [])]
		.sort((a, b) => a.seq - b.seq)
		.map((part) => part.text.trim())
		.filter(Boolean)
		.join(' ');

export type ExportLabels = {
	transcript: string;
	summary: string;
	minutes: string;
	actions: string;
	owner: string;
	due: string;
	noActions: string;
};

export const transcriptMarkdown = (
	title: string,
	state: MeetingState,
	labels: ExportLabels,
	raw = false
): string => {
	const transcript = state.transcript;
	const lines = [`# ${title}`, '', `## ${labels.transcript}`, ''];
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
	title: string,
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
	return `# ${title}\n\n## ${labels[kind]}\n\n${body.trim()}\n`;
};

/** Safe file name stem: keeps letters (incl. accents), digits, spaces, dots and dashes. */
export const fileStem = (value: string): string =>
	value
		.replace(/[^\p{L}\p{N} ._-]+/gu, '')
		.trim()
		.replace(/\s+/g, '-')
		.slice(0, 80) || 'vergadering';
