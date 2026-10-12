// [Gradient] Vergadering: a crash-safe copy of each recording on this device, so an interrupted
// meeting can still be finished. Nothing here uploads; that always takes a user action.

/** One recorder session of a meeting: part 1 is the first recording, a resume adds part 2, … */
export type RecordedPart = { part: number; blob: Blob };

export interface PartStore {
	/** Stores the `index`-th timeslice of a part; slices of a part concatenate into a valid file. */
	append(meetingId: string, part: number, index: number, data: Blob): Promise<void>;
	/** The meeting's parts in order, each its slices joined. */
	parts(meetingId: string): Promise<RecordedPart[]>;
	remove(meetingId: string): Promise<void>;
	/** Drops one part, e.g. a resumed recording the user discarded. */
	removePart(meetingId: string, part: number): Promise<void>;
	/** Drops every meeting's recordings, e.g. on sign-out, so the next user of this device finds none. */
	clear(): Promise<void>;
}

type Slice = { meetingId: string; part: number; index: number; data: Blob };

const joinSlices = (slices: Slice[]): RecordedPart[] => {
	const sorted = [...slices].sort((a, b) => a.part - b.part || a.index - b.index);
	const parts = new Map<number, Blob[]>();
	for (const slice of sorted) parts.set(slice.part, [...(parts.get(slice.part) ?? []), slice.data]);
	return [...parts.entries()].map(([part, blobs]) => ({
		part,
		blob: new Blob(blobs, { type: blobs[0]?.type || 'audio/webm' })
	}));
};

/** The next part number after what is stored (1 when nothing is). */
export const nextPart = (parts: RecordedPart[]): number =>
	parts.reduce((max, { part }) => Math.max(max, part), 0) + 1;

export class MemoryPartStore implements PartStore {
	private slices: Slice[] = [];

	async append(meetingId: string, part: number, index: number, data: Blob) {
		this.slices = this.slices.filter(
			(slice) => !(slice.meetingId === meetingId && slice.part === part && slice.index === index)
		);
		this.slices.push({ meetingId, part, index, data });
	}

	async parts(meetingId: string) {
		return joinSlices(this.slices.filter((slice) => slice.meetingId === meetingId));
	}

	async remove(meetingId: string) {
		this.slices = this.slices.filter((slice) => slice.meetingId !== meetingId);
	}

	async removePart(meetingId: string, part: number) {
		this.slices = this.slices.filter(
			(slice) => !(slice.meetingId === meetingId && slice.part === part)
		);
	}

	async clear() {
		this.slices = [];
	}
}

const DB_NAME = 'soev-meetings';
const STORE = 'slices';

export class IdbPartStore implements PartStore {
	private db: Promise<import('idb').IDBPDatabase> | null = null;

	private open() {
		this.db ??= import('idb').then(({ openDB }) =>
			openDB(DB_NAME, 1, {
				upgrade(db) {
					const store = db.createObjectStore(STORE, { keyPath: ['meetingId', 'part', 'index'] });
					store.createIndex('meeting', 'meetingId');
				}
			})
		);
		return this.db;
	}

	async append(meetingId: string, part: number, index: number, data: Blob) {
		await (await this.open()).put(STORE, { meetingId, part, index, data });
	}

	async parts(meetingId: string) {
		return joinSlices(await (await this.open()).getAllFromIndex(STORE, 'meeting', meetingId));
	}

	async remove(meetingId: string) {
		const db = await this.open();
		const tx = db.transaction(STORE, 'readwrite');
		for (const key of await tx.store.index('meeting').getAllKeys(meetingId))
			await tx.store.delete(key);
		await tx.done;
	}

	async removePart(meetingId: string, part: number) {
		const db = await this.open();
		const tx = db.transaction(STORE, 'readwrite');
		for (const key of await tx.store.index('meeting').getAllKeys(meetingId)) {
			if ((key as [string, number, number])[1] === part) await tx.store.delete(key);
		}
		await tx.done;
	}

	async clear() {
		await (await this.open()).clear(STORE);
	}
}

/** Recovery is best effort: when storage fails, recording and finishing work as before. */
export class SafePartStore implements PartStore {
	constructor(
		private readonly inner: PartStore,
		private readonly onError: (error: unknown) => void = (error) =>
			console.warn('Meeting recovery storage unavailable', error)
	) {}

	private async guard<T>(run: () => Promise<T>, fallback: T): Promise<T> {
		try {
			return await run();
		} catch (error) {
			this.onError(error);
			return fallback;
		}
	}

	append(meetingId: string, part: number, index: number, data: Blob) {
		return this.guard(() => this.inner.append(meetingId, part, index, data), undefined);
	}

	parts(meetingId: string) {
		return this.guard(() => this.inner.parts(meetingId), [] as RecordedPart[]);
	}

	remove(meetingId: string) {
		return this.guard(() => this.inner.remove(meetingId), undefined);
	}

	removePart(meetingId: string, part: number) {
		return this.guard(() => this.inner.removePart(meetingId, part), undefined);
	}

	clear() {
		return this.guard(() => this.inner.clear(), undefined);
	}
}

let shared: PartStore | null = null;

export const partStore = (): PartStore =>
	(shared ??= new SafePartStore(
		typeof indexedDB !== 'undefined' ? new IdbPartStore() : new MemoryPartStore()
	));

export type InterruptedAction = 'finish_local' | 'resume' | 'finish_live';

/** What an interrupted meeting can still do on this device. */
export const interruptedActions = ({
	localParts,
	liveParts
}: {
	localParts: number;
	liveParts: number;
}): InterruptedAction[] => [
	...(localParts > 0 ? (['finish_local'] as const) : []),
	'resume',
	...(liveParts > 0 ? (['finish_live'] as const) : [])
];

/** The seq for the next live chunk: after the highest one the meeting has seen. */
export const nextChunkSeq = (live: { seq: number }[] | undefined): number =>
	(live ?? []).reduce((max, { seq }) => Math.max(max, seq), 0) + 1;
