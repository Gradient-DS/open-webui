// [Gradient] Vergadering: live segments and their serialized delivery, free of DOM so they unit-test.

export const SEGMENT_MS = 25_000;
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

type Timers = {
	setInterval: (fn: () => void, ms: number) => unknown;
	clearInterval: (handle: unknown) => void;
};

const defaultTimers: Timers = {
	setInterval: (fn, ms) => setInterval(fn, ms),
	clearInterval: (handle) => clearInterval(handle as ReturnType<typeof setInterval>)
};

/**
 * Restarts a recorder every `intervalMs` so each segment is a self-contained file:
 * a timeslice chunk would lack the container header.
 */
export class SegmentRotator {
	private current: RecorderLike | null = null;
	private handle: unknown = null;
	private seq = 0;
	private stopping: Promise<void> | null = null;
	private readonly discarded = new WeakSet<RecorderLike>();

	constructor(
		private readonly create: () => RecorderLike,
		private readonly onSegment: (blob: Blob, seq: number) => void,
		private readonly intervalMs: number = SEGMENT_MS,
		private readonly timers: Timers = defaultTimers
	) {}

	start(): void {
		this.current = this.open();
		this.handle = this.timers.setInterval(() => this.rotate(), this.intervalMs);
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

	private rotate(): void {
		const previous = this.current;
		this.current = this.open();
		if (previous && previous.state !== 'inactive') previous.stop();
	}

	/** Stops rotating; the trailing partial segment is dropped (the full recording covers it). */
	stop(): Promise<void> {
		if (this.stopping) return this.stopping;
		if (this.handle !== null) this.timers.clearInterval(this.handle);
		this.handle = null;
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
