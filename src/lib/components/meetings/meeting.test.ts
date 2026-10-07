import { describe, expect, it } from 'vitest';
import JSZip from 'jszip';

import {
	fileStem,
	formatTimestamp,
	groupTurns,
	hasMeetingAgent,
	liveText,
	outputMarkdown,
	transcriptMarkdown,
	type ExportLabels,
	type MeetingState
} from './meeting';
import { documentXml, escapeXml, markdownBlocks, markdownToDocx } from './docx';
import { SegmentRotator, SerialQueue, sendWhenIdle, type RecorderLike } from './recorder';

const labels: ExportLabels = {
	transcript: 'Transcript',
	summary: 'Samenvatting',
	minutes: 'Notulen',
	actions: 'Actiepunten',
	owner: 'Eigenaar',
	due: 'Deadline',
	noActions: 'Geen actiepunten'
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

	it('joins live parts in sequence order', () => {
		expect(liveText(state)).toBe('eerste tweede');
	});

	it('exports the transcript with names, labels and timestamps', () => {
		const md = transcriptMarkdown('Weekoverleg', state, labels);
		expect(md).toContain('# Weekoverleg');
		expect(md).toContain('**Xander** (00:00)\n\nGoedemorgen allemaal. We beginnen.');
		expect(md).toContain('**Spreker 2** (01:05)');
		expect(transcriptMarkdown('W', state, labels, true)).toContain(
			'Heedemorgen allemaal eh we beginnen'
		);
	});

	it('exports outputs, actions as a list, and nothing for a missing output', () => {
		expect(outputMarkdown('W', state, 'summary', labels)).toContain(
			'Kort **overleg** over één punt.'
		);
		expect(outputMarkdown('W', state, 'actions', labels)).toContain(
			'- Offerte sturen (Eigenaar: Xander, Deadline: vrijdag)\n- Agenda'
		);
		expect(outputMarkdown('W', state, 'minutes', labels)).toBeNull();
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
	it('emits one self-contained segment per interval and drops the trailing one', async () => {
		let tick: () => void = () => {};
		const created: FakeRecorder[] = [];
		const segments: [string, number][] = [];
		const rotator = new SegmentRotator(
			() => {
				const recorder = new FakeRecorder(`r${created.length + 1}`);
				created.push(recorder);
				return recorder;
			},
			async (blob, seq) => segments.push([await blob.text(), seq]),
			25_000,
			{ setInterval: (fn) => ((tick = fn), 1), clearInterval: () => (tick = () => {}) }
		);
		rotator.start();
		tick();
		tick();
		await rotator.stop();
		await new Promise((resolve) => setTimeout(resolve, 0));
		expect(segments).toEqual([
			['r1', 1],
			['r2', 2]
		]);
		expect(created.every((recorder) => recorder.state === 'inactive')).toBe(true);
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
