import equal from 'fast-deep-equal';
import type { Connection, RunOutcome, Schedule, ScheduleForm, SyncRun } from '$lib/apis/cloudSync';

export function oneDriveScope(item: {
	id: string;
	driveId: string;
	type: 'file' | 'folder';
	name: string;
	path: string;
}): Pick<ScheduleForm, 'scope' | 'label' | 'path'> {
	return {
		label: item.name,
		path: item.path,
		scope: {
			drive_id: item.driveId,
			item_id: item.id,
			include_descendants: item.type === 'folder',
			single_file: item.type === 'file'
		}
	};
}

export function googleDriveScope(item: {
	id: string;
	type: 'file' | 'folder';
	name: string;
	path: string;
}): Pick<ScheduleForm, 'scope' | 'label' | 'path'> {
	return {
		label: item.name,
		path: item.path,
		scope: { file_id: item.id, drive_id: null, include_descendants: item.type === 'folder' }
	};
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
			(['pending', 'suspended:reauth'].includes(connection.lifecycle) ||
				connection.last_error === 'owner_mismatch')
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

export interface CloudSyncProvider {
	type: 'onedrive' | 'google_drive';
	label: string;
	startSyncParam: string;
}

export const CLOUD_PROVIDERS: Record<string, CloudSyncProvider> = {
	onedrive: { type: 'onedrive', label: 'OneDrive', startSyncParam: 'start_onedrive_sync' },
	google_drive: {
		type: 'google_drive',
		label: 'Google Drive',
		startSyncParam: 'start_google_drive_sync'
	}
};

export interface SchedulePair {
	content?: Schedule;
	acl?: Schedule;
}

export function pairSchedules(schedules: Schedule[]): SchedulePair[] {
	const remaining = new Set(schedules.filter((schedule) => schedule.kind === 'acl_refresh'));
	const pairs: SchedulePair[] = schedules
		.filter((schedule) => schedule.kind === 'content')
		.map((content) => {
			const acl = [...remaining].find(
				(schedule) =>
					schedule.connection_id === content.connection_id && equal(schedule.scope, content.scope)
			);
			if (acl) remaining.delete(acl);
			return { content, acl };
		});
	return [...pairs, ...[...remaining].map((acl) => ({ acl }))];
}

export function connectionOutcome(
	connection: Connection,
	elapsedMs: number
): { status: 'done' | 'waiting' | 'gave_up' } | { status: 'failed'; reason: string } {
	if (
		connection.last_error ||
		connection.lifecycle.startsWith('suspended:') ||
		connection.lifecycle === 'revoked'
	)
		return { status: 'failed', reason: connection.last_error ?? connection.lifecycle };
	if (connection.lifecycle === 'enabled') return { status: 'done' };
	return { status: elapsedMs >= 120000 ? 'gave_up' : 'waiting' };
}
