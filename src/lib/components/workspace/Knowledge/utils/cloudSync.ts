import { providerFor } from '$lib/sources/registry';
import equal from 'fast-deep-equal';
import type { Connection, RunOutcome, Schedule, SyncRun } from '$lib/apis/cloudSync';

export function trustedConnectOrigins(location: string, apiBase: string): Set<string> {
	const origins = new Set([location]);
	try {
		const origin = new URL(apiBase).origin;
		if (origin !== 'null') origins.add(origin);
	} catch {
		// Relative API bases use the page origin.
	}
	return origins;
}

export function connectResult(
	event: Pick<MessageEvent, 'origin' | 'source' | 'data'>,
	trustedOrigins: Set<string>,
	popup: Window,
	connectionId?: string
): 'pending' | 'error' | 'invalid' | null {
	if (
		!connectionId ||
		!trustedOrigins.has(event.origin) ||
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
	// A first-time connect stays `pending` while its popup is open, so only a
	// suspended connection or a refused consent asks for a reconnect.
	return [...connections.values()].filter((connection) =>
		[
			...(['pending', 'revoked'].includes(connection.lifecycle) ? [] : [connection.lifecycle]),
			connection.last_error
		].some((code) => providerFor(connection.source_kind)?.needsReconnectOn.includes(code ?? ''))
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

// Content schedules whose run was live in `previous` and has an outcome in
// `current`: the moment to tell the user how the sync went.
export function finishedRuns(previous: Schedule[], current: Schedule[]): Schedule[] {
	const live = new Set(
		previous
			.filter((schedule) => runIsLive(schedule.last_run))
			.map((schedule) => `${schedule.id}\n${schedule.last_run!.id}`)
	);
	return current.filter(
		(schedule) =>
			schedule.kind === 'content' &&
			!!schedule.last_run &&
			!runIsLive(schedule.last_run) &&
			live.has(`${schedule.id}\n${schedule.last_run.id}`)
	);
}

export function shouldRefetchSyncItems(previous: Schedule[], current: Schedule[]): boolean {
	const isLive = current.some((schedule) => runIsLive(schedule.last_run));
	const snapshot = (schedules: Schedule[]) =>
		JSON.stringify(
			[...schedules]
				.sort((a, b) => a.id.localeCompare(b.id))
				.map((schedule) => [
					schedule.id,
					schedule.last_run?.id ?? null,
					schedule.last_run?.finished_at ?? null,
					schedule.document_count
				])
		);
	return (
		isLive ||
		(!isLive && previous.some((schedule) => runIsLive(schedule.last_run))) ||
		snapshot(previous) !== snapshot(current)
	);
}
