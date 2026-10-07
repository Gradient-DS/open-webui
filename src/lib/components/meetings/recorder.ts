// [Gradient] Vergadering: live segments and their serialized delivery, free of DOM so they unit-test.

export const CONFLICT_RETRY_MS = 1_000;

export const RECORDER_MIME_TYPES = [
	'audio/webm; codecs=opus',
	'audio/webm',
	'audio/ogg; codecs=opus',
	'audio/mp4'
];

export interface RecorderLike {
	readonly mimeType: string;
	state: string;
	ondataavailable: ((event: { data: Blob }) => void) | null;
	onstop: (() => void) | null;
	start(timeslice?: number): void;
	stop(): void;
}

/** Live segments are cut at the first pause after this long, or at the hard cap. */
export const SEGMENT_MIN_MS = 20_000;
export const SEGMENT_MAX_MS = 45_000;
export const SILENCE_HOLD_MS = 400;

export type CutState = {
	/** When the current segment started (ms). */
	segmentStart: number;
	/** Since when the signal has been below the silence threshold, if it is now. */
	quietSince: number | null;
	/** Running estimate of the background level (RMS, 0..1). */
	noiseFloor: number;
};

export const initialCutState = (now: number): CutState => ({
	segmentStart: now,
	quietSince: null,
	noiseFloor: 0.02
});

/** Silence is anything under a multiple of the noise floor, clamped to a sane RMS band. */
export const silenceThreshold = (noiseFloor: number): number =>
	Math.min(0.06, Math.max(0.008, noiseFloor * 2.5));

/** The floor drops quickly to quieter samples and rises slowly, so speech does not lift it. */
export const nextNoiseFloor = (floor: number, rms: number): number =>
	rms < floor ? floor * 0.8 + rms * 0.2 : floor * 0.999 + rms * 0.001;

/** Decide, for one RMS sample, whether to cut the live segment now. */
export const decideCut = (
	state: CutState,
	now: number,
	rms: number,
	{
		minMs = SEGMENT_MIN_MS,
		maxMs = SEGMENT_MAX_MS,
		holdMs = SILENCE_HOLD_MS
	}: { minMs?: number; maxMs?: number; holdMs?: number } = {}
): { cut: boolean; state: CutState } => {
	const noiseFloor = nextNoiseFloor(state.noiseFloor, rms);
	const quiet = rms < silenceThreshold(noiseFloor);
	const quietSince = quiet ? (state.quietSince ?? now) : null;
	const elapsed = now - state.segmentStart;
	const cut =
		elapsed >= maxMs || (elapsed >= minMs && quietSince !== null && now - quietSince >= holdMs);
	return {
		cut,
		state: cut
			? { segmentStart: now, quietSince: null, noiseFloor }
			: { segmentStart: state.segmentStart, quietSince, noiseFloor }
	};
};

/** RMS (0..1) of 8-bit time-domain analyser data. */
export const rmsOf = (data: Uint8Array): number => {
	let sum = 0;
	for (const value of data) sum += ((value - 128) / 128) ** 2;
	return data.length ? Math.sqrt(sum / data.length) : 0;
};

/**
 * Restarts a recorder whenever `rotate()` is called, so each segment is a self-contained file:
 * a timeslice chunk would lack the container header.
 */
export class SegmentRotator {
	private current: RecorderLike | null = null;
	private seq = 0;
	private stopping: Promise<void> | null = null;
	private readonly discarded = new WeakSet<RecorderLike>();

	constructor(
		private readonly create: () => RecorderLike,
		private readonly onSegment: (blob: Blob, seq: number) => void
	) {}

	start(): void {
		this.current = this.open();
	}

	private open(): RecorderLike {
		const recorder = this.create();
		const parts: Blob[] = [];
		recorder.ondataavailable = (event) => {
			if (event.data && event.data.size > 0) parts.push(event.data);
		};
		recorder.onstop = () => {
			const blob = new Blob(parts, { type: recorder.mimeType || parts[0]?.type || 'audio/webm' });
			if (!this.discarded.has(recorder) && blob.size > 0) this.onSegment(blob, ++this.seq);
		};
		recorder.start();
		return recorder;
	}

	rotate(): void {
		if (this.stopping || !this.current) return;
		const previous = this.current;
		this.current = this.open();
		if (previous.state !== 'inactive') previous.stop();
	}

	/** Stops; the trailing partial segment is dropped (the full recording covers it). */
	stop(): Promise<void> {
		if (this.stopping) return this.stopping;
		const last = this.current;
		this.current = null;
		this.stopping = new Promise((resolve) => {
			if (!last || last.state === 'inactive') return resolve();
			this.discarded.add(last);
			const done = last.onstop;
			last.onstop = () => {
				done?.();
				resolve();
			};
			last.stop();
		});
		return this.stopping;
	}
}

export const isConflict = (error: unknown): boolean =>
	(error as { status?: number })?.status === 409;

const sleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

/** Retries `send` while the thread is busy (409); any other error is thrown. */
export const sendWhenIdle = async <T>(
	send: () => Promise<T>,
	{
		delayMs = CONFLICT_RETRY_MS,
		maxAttempts = 600,
		wait = sleep
	}: { delayMs?: number; maxAttempts?: number; wait?: (ms: number) => Promise<void> } = {}
): Promise<T> => {
	for (let attempt = 1; ; attempt++) {
		try {
			return await send();
		} catch (error) {
			if (!isConflict(error) || attempt >= maxAttempts) throw error;
			await wait(delayMs);
		}
	}
};

/** Delivers jobs one at a time, in order; a failed job is reported and the queue moves on. */
export class SerialQueue {
	private tail: Promise<void> = Promise.resolve();
	pending = 0;

	constructor(
		private readonly onChange: (pending: number) => void = () => {},
		private readonly onError: (error: unknown) => void = () => {}
	) {}

	push(job: () => Promise<void>): void {
		this.pending++;
		this.onChange(this.pending);
		this.tail = this.tail
			.then(job)
			.catch((error) => this.onError(error))
			.finally(() => {
				this.pending--;
				this.onChange(this.pending);
			});
	}

	drain(): Promise<void> {
		return this.tail;
	}
}
