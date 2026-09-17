import type { Connection, Schedule, ScheduleForm, SyncRun } from '$lib/apis/cloudSync';

export function oneDriveScope(item: {
	id: string;
	driveId: string;
	type: 'file' | 'folder';
}): ScheduleForm['scope'] {
	return {
		drive_id: item.driveId,
		item_id: item.id,
		include_descendants: item.type === 'folder',
		single_file: item.type === 'file'
	};
}

export function googleDriveScope(item: {
	id: string;
	type: 'file' | 'folder';
}): ScheduleForm['scope'] {
	return { file_id: item.id, drive_id: null, include_descendants: item.type === 'folder' };
}

export function connectResult(
	event: Pick<MessageEvent, 'origin' | 'source' | 'data'>,
	origin: string,
	popup: Window,
	connectionId?: string
): 'pending' | 'error' | 'invalid' | null {
	if (
		!connectionId ||
		event.origin !== origin ||
		event.source !== popup ||
		event.data?.type !== 'soev_connect' ||
		event.data.connection !== connectionId
	)
		return null;
	const result = event.data.result;
	return result === 'pending' || result === 'error' || result === 'invalid' ? result : null;
}

export function reconnectConnections(
	schedules: Schedule[],
	pending: Connection | null
): Connection[] {
	const connections = new Map(
		schedules.map((schedule) => [schedule.connection.id, schedule.connection])
	);
	if (pending && !connections.has(pending.id)) connections.set(pending.id, pending);
	return [...connections.values()].filter(
		(connection) =>
			['onedrive', 'google_drive'].includes(connection.source_kind) &&
			['pending', 'suspended:reauth'].includes(connection.lifecycle)
	);
}

export type RunStatus = RunOutcome | 'running' | 'cancelling';

// A run row exists from the moment the worker claims the job, so "no outcome
// yet" is running, not queued. `cancel_requested_at` is set while the run is
// still live and clears nothing, so it only qualifies an unfinished run.
export function runStatus(run: SyncRun): RunStatus {
	if (run.outcome) return run.outcome;
	return run.cancel_requested_at ? 'cancelling' : 'running';
}

export function runIsLive(run: SyncRun | null | undefined): boolean {
	return !!run && !run.outcome;
}

// Keys the worker actually writes (sync_execution.py): content runs emit
// fetched/submitted/deleted, ACL runs reprincipalled/revoked, both share
// unchanged/failed/landed/timed_out. `secret_days` is also stuffed into
// `counts` but is an expiry warning, not a count — schedules.py reads it out
// as provider_secret_days_to_expiry, so it is deliberately absent here.
const COUNT_LABELS: Record<string, string> = {
	landed: 'Synced',
	fetched: 'Fetched',
	submitted: 'Submitted',
	reprincipalled: 'Permissions updated',
	revoked: 'Permissions revoked',
	unchanged: 'Unchanged',
	deleted: 'Deleted',
	failed: 'Failed',
	timed_out: 'Timed out'
};

export function runCounts(run: SyncRun): { label: string; count: number }[] {
	const counts = run.counts ?? {};
	return Object.entries(COUNT_LABELS).flatMap(([field, label]) => {
		const count = counts[field];
		return typeof count === 'number' && Number.isFinite(count) ? [{ label, count }] : [];
	});
}
