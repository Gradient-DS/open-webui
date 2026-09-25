import type { FolderProgress } from './sourceState';

export interface FolderUpload extends FolderProgress {
	name: string;
}

export type FolderUploadSummary = {
	label: string | null;
	added: number;
	failed: number;
	unresolved: number;
};

type TerminalStatus = 'completed' | 'failed';
type Callbacks = {
	onChange: () => void;
	onRefresh: () => void;
	onFinish: (summary: FolderUploadSummary, timedOut: boolean) => void;
};

export const ancestorPaths = (path: string): string[] => {
	const segments = path.split('/').filter(Boolean);
	return segments.map((_, index) => segments.slice(0, index + 1).join('/'));
};

const countRow = (rows: Map<string, FolderUpload>, id: string, name: string) => {
	const row = rows.get(id) ?? { name, total: 0, transferred: 0, processed: 0, failed: 0 };
	row.total++;
	rows.set(id, row);
};

export function mergeUploadRows(sessions: FolderUploadSession[]): Map<string, FolderUpload> {
	const rows = new Map<string, FolderUpload>();
	for (const session of sessions) {
		if (session.disposed) continue;
		for (const [id, row] of session.rows) {
			const merged = rows.get(id);
			if (!merged) rows.set(id, { ...row });
			else {
				merged.total += row.total;
				merged.transferred += row.transferred;
				merged.processed += row.processed;
				merged.failed += row.failed;
			}
		}
	}
	return rows;
}

export function routeFileStatus(
	sessions: FolderUploadSession[],
	singleUploads: ReadonlySet<string>,
	fileId: string,
	status: string
): 'session' | 'batch' | 'parked' {
	if (status !== 'completed' && status !== 'failed') return 'batch';
	let consumed = false;
	for (const session of sessions) {
		if (session.onFileStatus(fileId, status)) consumed = true;
	}
	if (consumed) return 'session';
	if (singleUploads.has(fileId)) return 'batch';
	return sessions.some((session) => session.inFlight) ? 'parked' : 'batch';
}

export class FolderUploadSession {
	rows = new Map<string, FolderUpload>();
	topLevelIds: string[] = [];
	files = new Map<string, string[]>();
	early = new Map<string, TerminalStatus>();
	inFlight = true;
	disposed = false;
	readonly topNames: string[];
	private settled = new Set<string>();
	private cap: ReturnType<typeof setTimeout> | undefined;
	private refresh: ReturnType<typeof setTimeout> | undefined;

	constructor(
		private key: string,
		private paths: string[],
		private callbacks: Callbacks
	) {
		this.topNames = [...new Set(paths.filter(Boolean).map((path) => path.split('/')[0]))];
		for (const path of paths) {
			if (path) countRow(this.rows, this.placeholderId(path.split('/')[0]), path.split('/')[0]);
		}
		this.topLevelIds = [...this.rows.keys()];
	}

	placeholderId(name: string): string {
		return `placeholder:${this.key}:${name}`;
	}

	setDirectories(directoryIdByPath: Record<string, string>): void {
		if (this.disposed) return;
		this.rows.clear();
		for (const path of this.paths) {
			for (const prefix of ancestorPaths(path)) {
				countRow(this.rows, directoryIdByPath[prefix], prefix.split('/').at(-1)!);
			}
		}
		for (const name of this.topNames) {
			const row = this.rows.get(directoryIdByPath[name]);
			if (row) this.rows.set(this.placeholderId(name), row);
		}
		this.topLevelIds = this.topNames.map((name) => directoryIdByPath[name]);
		this.callbacks.onChange();
	}

	onUploaded(rowIds: string[], file: { id?: string; error?: string } | null): void {
		if (this.disposed) return;
		const failed = !file?.id || !!file.error;
		for (const id of rowIds) {
			const row = this.rows.get(id);
			if (!row) continue;
			row.transferred++;
			if (failed) {
				row.processed++;
				row.failed++;
			}
		}
		if (!failed && file?.id) {
			this.files.set(file.id, rowIds);
			const early = this.early.get(file.id);
			if (early) this.onFileStatus(file.id, early);
		}
		this.callbacks.onChange();
		this.refreshSoon();
	}

	onFileStatus(fileId: string, status: string): boolean {
		if (this.disposed || (status !== 'completed' && status !== 'failed')) return false;
		if (this.settled.has(fileId)) return true;
		const ids = this.files.get(fileId);
		if (!ids) {
			// Every in-flight session gets the event, even for already listed files.
			// Only the session that later receives this file id claims the result.
			if (this.inFlight) this.early.set(fileId, status);
			return false;
		}
		this.files.delete(fileId);
		this.early.delete(fileId);
		this.settled.add(fileId);
		for (const id of ids) {
			const row = this.rows.get(id);
			if (!row) continue;
			row.processed++;
			if (status === 'failed') row.failed++;
		}
		this.callbacks.onChange();
		this.refreshSoon();
		if (this.done()) this.finish(false);
		return true;
	}

	completeUploads(): void {
		if (this.disposed || !this.inFlight) return;
		this.inFlight = false;
		this.early.clear();
		if (this.done()) this.finish(false);
		else this.cap = setTimeout(() => this.finish(true), 10 * 60 * 1000);
	}

	done(): boolean {
		return !this.inFlight && [...this.rows.values()].every((row) => row.processed >= row.total);
	}

	summary(): FolderUploadSummary {
		const rows = this.topLevelIds.flatMap((id) => this.rows.get(id) ?? []);
		return {
			label: rows.length === 1 ? rows[0].name : null,
			added: rows.reduce((sum, row) => sum + row.processed - row.failed, 0),
			failed: rows.reduce((sum, row) => sum + row.failed, 0),
			unresolved: rows.reduce((sum, row) => sum + row.total - row.processed, 0)
		};
	}

	refreshSoon(): void {
		if (this.disposed || this.refresh !== undefined) return;
		this.refresh = setTimeout(() => {
			this.refresh = undefined;
			if (!this.disposed) this.callbacks.onRefresh();
		}, 1500);
	}

	private finish(timedOut: boolean): void {
		if (this.disposed) return;
		const summary = this.summary();
		this.dispose();
		if (timedOut) this.callbacks.onRefresh();
		this.callbacks.onFinish(summary, timedOut);
	}

	dispose(): void {
		this.disposed = true;
		this.inFlight = false;
		clearTimeout(this.cap);
		clearTimeout(this.refresh);
		this.cap = undefined;
		this.refresh = undefined;
		this.rows.clear();
		this.topLevelIds = [];
		this.files.clear();
		this.early.clear();
		this.settled.clear();
	}
}
