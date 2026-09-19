import type { Connection, Schedule } from '$lib/apis/cloudSync';
import { CLOUD_PROVIDERS, runIsLive, type SchedulePair } from './cloudSync';

export type SourceState =
	| 'syncing'
	| 'up_to_date'
	| 'partly_synced'
	| 'scheduled'
	| 'paused'
	| 'needs_reconnect'
	| 'needs_access'
	| 'error';

export interface SourceView {
	state: SourceState;
	label: string;
	path: string;
	provider: string;
	lastSyncedAt: string | null;
	nextDueAt: string | null;
	documents: number;
	skipped: number;
	otherKbs: number;
	primary: 'sync_now' | 'reconnect' | 'resume' | 'request_access' | null;
}

export function sourceState(pair: SchedulePair, connection: Connection): SourceView {
	const schedules = [pair.content, pair.acl].filter((item): item is Schedule => !!item);
	const schedule = pair.content ?? pair.acl!;
	const run = schedule.last_run;
	// Schedule errors supersede stale run errors for the same schedule.
	const errors = [
		connection.last_error,
		...schedules.map((item) => item.last_error ?? item.last_run?.error_code)
	];
	let state: SourceState;
	if (schedules.some((item) => runIsLive(item.last_run))) state = 'syncing';
	else if (
		['suspended:reauth', 'pending', 'revoked'].includes(connection.lifecycle) ||
		errors.some((error) =>
			['access_revoked', 'credential_unusable', 'owner_mismatch'].includes(error ?? '')
		)
	)
		state = 'needs_reconnect';
	else if (errors.includes('writer_revoked')) state = 'needs_access';
	else if (schedules.some((item) => item.lifecycle === 'suspended' && !item.last_error))
		state = 'paused';
	else if (
		schedules.some((item) => item.last_run?.outcome === 'failed' || item.last_error) ||
		((schedule.document_count ?? run?.counts?.landed ?? 0) === 0 &&
			schedules.some(
				(item) => item.last_run?.outcome === 'partial' || (item.last_run?.counts?.failed ?? 0) > 0
			))
	)
		state = 'error';
	else if (
		schedules.some(
			(item) => item.last_run?.outcome === 'partial' || (item.last_run?.counts?.failed ?? 0) > 0
		)
	)
		state = 'partly_synced';
	else if (run?.outcome === 'succeeded') state = 'up_to_date';
	else state = 'scheduled';
	return {
		state,
		label:
			schedule.label ||
			(schedule.scope.single_file || schedule.scope.include_descendants === false
				? 'File'
				: 'Folder'),
		path: schedule.path ?? '',
		provider: CLOUD_PROVIDERS[schedule.source_kind]?.label ?? schedule.source_kind,
		lastSyncedAt: run?.finished_at ?? (run?.outcome ? run.started_at : null),
		nextDueAt: schedule.next_due_at ?? null,
		documents: schedule.document_count ?? run?.counts?.landed ?? 0,
		skipped: pair.content?.last_run?.counts?.failed ?? 0,
		otherKbs: Math.max(0, (schedule.subscriber_count ?? 1) - 1),
		primary:
			state === 'syncing'
				? null
				: state === 'needs_reconnect'
					? 'reconnect'
					: state === 'needs_access'
						? 'request_access'
						: state === 'paused'
							? 'resume'
							: pair.content
								? 'sync_now'
								: null
	};
}

export function skippedReason(code: string): string {
	return (
		(
			{
				unsupported_content_type: 'File type not supported',
				item_too_large: 'Too large',
				empty_content: 'Empty document: no text found',
				processing_failed: 'Processing failed',
				timed_out: 'Processing timed out',
				acl_write_failed: 'Internal error',
				internal_error: 'Internal error',
				access_revoked: 'No access'
			} as Record<string, string>
		)[code] ?? code
	);
}

export function sourceTiming(
	view: Pick<SourceView, 'lastSyncedAt' | 'nextDueAt' | 'documents'> &
		Partial<Pick<SourceView, 'state'>>,
	relative: (time: string) => string,
	now = Date.now()
) {
	return {
		lastSync:
			view.state === 'syncing'
				? { key: '{{count}} documents so far', values: { count: view.documents } }
				: view.lastSyncedAt
					? {
							key: 'Last synced {{time}} · {{count}} documents',
							values: { time: relative(view.lastSyncedAt), count: view.documents }
						}
					: {
							key:
								view.documents > 0 ? '{{count}} documents' : 'Not synced yet · {{count}} documents',
							values: { count: view.documents }
						},
		nextCheck: !view.nextDueAt
			? null
			: Date.parse(view.nextDueAt) <= now
				? { key: 'Next check: as soon as possible', values: {} }
				: { key: 'Next check {{time}}', values: { time: relative(view.nextDueAt) } }
	};
}
