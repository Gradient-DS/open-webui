import { describe, expect, it } from 'vitest';
import JSZip from 'jszip';

import {
	fileStem,
	formatTimestamp,
	groupTurns,
	hasMeetingAgent,
	groupByRange,
	liveParts,
	matchesQuery,
	meetingTimes,
	metadataMarkdown,
	wordCount,
	type MeetingMeta,
	outputMarkdown,
	transcriptMarkdown,
	type ExportLabels,
	type MeetingState
} from './meeting';
import { documentXml, escapeXml, markdownBlocks, markdownToDocx } from './docx';
import {
	SEGMENT_MAX_MS,
	SEGMENT_MIN_MS,
	SegmentRotator,
	SerialQueue,
	decideCut,
	initialCutState,
	rmsOf,
	sendWhenIdle,
	type CutState,
	type RecorderLike
} from './recorder';
import { displayMediaOptions } from './audio';

const labels: ExportLabels = {
	transcript: 'Transcript',
	summary: 'Samenvatting',
	minutes: 'Notulen',
	actions: 'Actiepunten',
	owner: 'Eigenaar',
	due: 'Deadline',
	noActions: 'Geen actiepunten',
	date: 'Datum',
	time: 'Tijd',
	duration: 'Duur',
	speakers: 'Sprekers'
};

const meta: MeetingMeta = {
	title: 'Weekoverleg',
	date: '7 oktober 2026',
	time: '10:00–10:47',
	duration: '47:12',
	speakers: ['Xander', 'Spreker 2']
};

const state: MeetingState = {
	status: 'ready',
	transcript: {
		speakers: [
			{ label: 'Spreker 1', name: 'Xander', inferred: true },
			{ label: 'Spreker 2', name: null }
		],
		segments: [
			{
				start: 0,
				end: 2.4,
				speaker: 'Spreker 1',
				raw: 'Heedemorgen allemaal',
				clean: 'Goedemorgen allemaal.'
			},
			{ start: 2.4, end: 5, speaker: 'Spreker 1', raw: 'eh we beginnen', clean: 'We beginnen.' },
			{ start: 65, end: 70, speaker: 'Spreker 2', raw: 'dank je xander', clean: 'Dank je, Xander.' }
		]
	},
	outputs: {
		summary: { markdown: 'Kort **overleg** over één punt.' },
		minutes: null,
		actions: {
			items: [
				{ task: 'Offerte sturen', owner: 'Xander', due: 'vrijdag' },
				{ task: 'Agenda', owner: null, due: null }
			]
		}
	},
	live: [
		{ seq: 2, text: 'tweede' },
		{ seq: 1, text: 'eerste ' }
	]
};

describe('meeting helpers', () => {
	it('detects the meeting agent in the catalog', () => {
		expect(hasMeetingAgent([{ name: 'soev_react', kind: 'chat' }])).toBe(false);
		expect(hasMeetingAgent([{ name: 'meeting', kind: 'surface' }])).toBe(true);
		expect(hasMeetingAgent(null)).toBe(false);
	});

	it('formats timestamps with hours only when needed', () => {
		expect(formatTimestamp(65)).toBe('01:05');
		expect(formatTimestamp(3725)).toBe('1:02:05');
	});

	it('groups consecutive segments of a speaker into one turn', () => {
		const turns = groupTurns(state.transcript!.segments);
		expect(turns.map((turn) => [turn.speaker, turn.start, turn.end])).toEqual([
			['Spreker 1', 0, 5],
			['Spreker 2', 65, 70]
		]);
		expect(turns[0].texts).toEqual(['Goedemorgen allemaal.', 'We beginnen.']);
		expect(groupTurns(state.transcript!.segments, true)[0].texts[0]).toBe('Heedemorgen allemaal');
	});

	it('keeps live parts as separate paragraphs in sequence order', () => {
		expect(liveParts(state)).toEqual(['eerste', 'tweede']);
	});

	it('takes start, end and duration from the snapshot, falling back to consent time', () => {
		expect(
			meetingTimes({
				...state,
				started_at: '2026-10-07T10:00:00Z',
				ended_at: '2026-10-07T10:47:12Z'
			})
		).toEqual({
			startedAt: '2026-10-07T10:00:00Z',
			endedAt: '2026-10-07T10:47:12Z',
			durationS: 2832
		});
		expect(
			meetingTimes({ ...state, consent: { text_version: 'v', at: '2026-10-01T09:00:00Z' } })
		).toEqual({ startedAt: '2026-10-01T09:00:00Z', endedAt: null, durationS: null });
		expect(meetingTimes({ ...state, duration_s: 60 }).durationS).toBe(60);
	});

	it('counts transcript words, or live words before there is a transcript', () => {
		expect(wordCount(state)).toBe(7);
		expect(wordCount({ ...state, transcript: null })).toBe(2);
	});

	it('writes the metadata block every download starts with', () => {
		const block = metadataMarkdown(meta, labels);
		expect(block).toContain('# Weekoverleg');
		expect(block).toContain('**Datum:** 7 oktober 2026');
		expect(block).toContain('**Tijd:** 10:00–10:47');
		expect(block).toContain('**Duur:** 47:12');
		expect(block).toContain('**Sprekers:** Xander, Spreker 2');
		expect(metadataMarkdown({ ...meta, duration: null, speakers: [] }, labels)).not.toContain(
			'Duur'
		);
	});

	it('filters titles ignoring case and accents, and groups in order', () => {
		expect(matchesQuery('Overleg café', 'CAFE')).toBe(true);
		expect(matchesQuery(null, 'x')).toBe(false);
		expect(matchesQuery('x', ' ')).toBe(true);
		expect(groupByRange([1, 2, 3, 4], (n) => (n < 3 ? 'Today' : 'Yesterday'))).toEqual([
			['Today', [1, 2]],
			['Yesterday', [3, 4]]
		]);
	});

	it('exports the transcript with names, labels and timestamps', () => {
		const md = transcriptMarkdown(meta, state, labels);
		expect(md).toContain('# Weekoverleg');
		expect(md).toContain('**Xander** (00:00)\n\nGoedemorgen allemaal. We beginnen.');
		expect(md).toContain('**Spreker 2** (01:05)');
		expect(transcriptMarkdown(meta, state, labels, true)).toContain(
			'Heedemorgen allemaal eh we beginnen'
		);
	});

	it('exports outputs, actions as a list, and nothing for a missing output', () => {
		expect(outputMarkdown(meta, state, 'summary', labels)).toContain(
			'Kort **overleg** over één punt.'
		);
		expect(outputMarkdown(meta, state, 'actions', labels)).toContain(
			'- Offerte sturen (Eigenaar: Xander, Deadline: vrijdag)\n- Agenda'
		);
		expect(outputMarkdown(meta, state, 'minutes', labels)).toBeNull();
	});

	it('keeps accented letters in file names and drops path characters', () => {
		expect(fileStem('Overleg café / 7-10')).toBe('Overleg-café-7-10');
		expect(fileStem('///')).toBe('vergadering');
	});
});

describe('docx export', () => {
	it('maps headings, bold runs and list items', () => {
		const blocks = markdownBlocks('# Titel\n\nTekst met **vet**.\n\n- een\n- twee');
		expect(blocks[0]).toEqual({ kind: 'heading', level: 1, runs: [{ text: 'Titel' }] });
		expect(blocks[1]).toEqual({
			kind: 'paragraph',
			runs: [{ text: 'Tekst met ' }, { text: 'vet', bold: true }, { text: '.' }]
		});
		expect(blocks.slice(2).map((block) => block.kind === 'item' && block.marker)).toEqual([
			'•',
			'•'
		]);
	});

	it('escapes XML and keeps Dutch characters', () => {
		expect(escapeXml('a < b & "c"\u0001')).toBe('a &lt; b &amp; &quot;c&quot;');
		expect(documentXml(markdownBlocks('Één café'))).toContain('Één café');
	});

	it('builds a Word package with the document part', async () => {
		const blob = await markdownToDocx('# Notulen\n\nBesluit: ja');
		const zip = await JSZip.loadAsync(await blob.arrayBuffer());
		expect(Object.keys(zip.files)).toEqual(
			expect.arrayContaining([
				'[Content_Types].xml',
				'_rels/.rels',
				'word/document.xml',
				'word/styles.xml'
			])
		);
		expect(await zip.file('word/document.xml')!.async('string')).toContain('Besluit: ja');
	});
});

class FakeRecorder implements RecorderLike {
	mimeType = 'audio/webm';
	state = 'inactive';
	ondataavailable: ((event: { data: Blob }) => void) | null = null;
	onstop: (() => void) | null = null;

	constructor(private readonly label: string) {}

	start() {
		this.state = 'recording';
	}

	stop() {
		this.state = 'inactive';
		this.ondataavailable?.({ data: new Blob([this.label], { type: 'audio/webm' }) });
		this.onstop?.();
	}
}

describe('segment rotation', () => {
	it('emits one self-contained segment per rotation and drops the trailing one', async () => {
		const created: FakeRecorder[] = [];
		const segments: [string, number][] = [];
		const rotator = new SegmentRotator(
			() => {
				const recorder = new FakeRecorder(`r${created.length + 1}`);
				created.push(recorder);
				return recorder;
			},
			async (blob, seq) => segments.push([await blob.text(), seq])
		);
		rotator.start();
		rotator.rotate();
		rotator.rotate();
		await rotator.stop();
		rotator.rotate();
		await new Promise((resolve) => setTimeout(resolve, 0));
		expect(segments).toEqual([
			['r1', 1],
			['r2', 2]
		]);
		expect(created).toHaveLength(3);
		expect(created.every((recorder) => recorder.state === 'inactive')).toBe(true);
	});
});

/** Feeds `rms` every 100 ms from `from` to `to` (ms) and returns the times a cut was decided. */
const run = (state: CutState, from: number, to: number, rms: (t: number) => number) => {
	const cuts: number[] = [];
	for (let t = from; t <= to; t += 100) {
		const decision = decideCut(state, t, rms(t));
		state = decision.state;
		if (decision.cut) cuts.push(t);
	}
	return { cuts, state };
};

describe('silence cuts', () => {
	const speech = 0.15;
	const quiet = 0.003;

	it('never cuts before 20 s, even in silence', () => {
		expect(run(initialCutState(0), 0, SEGMENT_MIN_MS - 100, () => quiet).cuts).toEqual([]);
	});

	it('cuts at the first pause of at least 400 ms after 20 s', () => {
		// Speech until 23 s, then a pause.
		const { cuts } = run(initialCutState(0), 0, 30_000, (t) => (t < 23_000 ? speech : quiet));
		expect(cuts).toEqual([23_400]);
	});

	it('ignores pauses shorter than 400 ms', () => {
		const blip = (t: number) => (t >= 21_000 && t < 21_300 ? quiet : speech);
		expect(run(initialCutState(0), 0, 30_000, blip).cuts).toEqual([]);
	});

	it('cuts at 45 s when nobody pauses, and restarts the clock', () => {
		const { cuts, state } = run(initialCutState(0), 0, SEGMENT_MAX_MS + 500, () => speech);
		expect(cuts).toEqual([SEGMENT_MAX_MS]);
		expect(state.segmentStart).toBe(SEGMENT_MAX_MS);
	});

	it('adapts to a noisy room: the background level is not speech, but not silence either', () => {
		// A steady 0.02 hum with speech at 0.2 until 25 s, then only the hum.
		const room = (t: number) => (t < 25_000 ? (Math.floor(t / 1000) % 4 === 3 ? 0.02 : 0.2) : 0.02);
		const { cuts } = run(initialCutState(0), 0, 30_000, room);
		expect(cuts[0]).toBeGreaterThanOrEqual(SEGMENT_MIN_MS);
		expect(cuts[0]).toBeLessThan(26_000);
	});

	it('measures RMS around the 128 midpoint', () => {
		expect(rmsOf(new Uint8Array([128, 128]))).toBe(0);
		expect(rmsOf(new Uint8Array([0, 0]))).toBe(1);
	});
});

describe('display capture hints', () => {
	it('asks for a browser tab with its audio and leaves this tab out', () => {
		expect(displayMediaOptions()).toMatchObject({
			video: { displaySurface: 'browser' },
			audio: { echoCancellation: false, noiseSuppression: false },
			preferCurrentTab: false,
			selfBrowserSurface: 'exclude',
			systemAudio: 'include',
			surfaceSwitching: 'include'
		});
	});
});

describe('chunk delivery', () => {
	it('retries while the thread is busy and rethrows other errors', async () => {
		let calls = 0;
		const waits: number[] = [];
		const result = await sendWhenIdle(
			async () => {
				calls++;
				if (calls < 3) throw { status: 409 };
				return 'ok';
			},
			{ wait: async (ms) => void waits.push(ms) }
		);
		expect(result).toBe('ok');
		expect(waits).toEqual([1000, 1000]);
		await expect(sendWhenIdle(async () => Promise.reject({ status: 500 }))).rejects.toEqual({
			status: 500
		});
	});

	it('runs jobs one at a time in order and survives a failed job', async () => {
		const order: string[] = [];
		const errors: unknown[] = [];
		const queue = new SerialQueue(undefined, (error) => errors.push(error));
		let release: () => void = () => {};
		queue.push(
			() => new Promise<void>((resolve) => (release = () => (order.push('a'), resolve())))
		);
		queue.push(async () => {
			throw new Error('b failed');
		});
		queue.push(async () => void order.push('c'));
		expect(queue.pending).toBe(3);
		await Promise.resolve();
		release();
		await queue.drain();
		expect(order).toEqual(['a', 'c']);
		expect(errors).toHaveLength(1);
		expect(queue.pending).toBe(0);
	});
});
