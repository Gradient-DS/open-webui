import { providerFor } from '$lib/sources/registry';
import type { Connection, Schedule } from '$lib/apis/cloudSync';
import { runIsLive, type SchedulePair } from './cloudSync';

export function skippedRunKey(schedule: Schedule | undefined): string {
	if (!schedule || runIsLive(schedule.last_run)) return '';
	return JSON.stringify([
		schedule.id,
		schedule.last_run?.id ?? null,
		schedule.last_run?.finished_at ?? null
	]);
}

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
		[connection.lifecycle, ...errors].some((code) =>
			providerFor(connection.source_kind)?.needsReconnectOn.includes(code ?? '')
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
		provider: providerFor(schedule.source_kind)?.label ?? schedule.source_kind,
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

// What a live run reports while it runs: the items its plan chose to fetch
// (`planned`), how many are fetched from the provider and how many have
// landed, as sync_execution.py publishes them. Null outside a live run or
// under a worker that publishes counts only at finish.
export interface FolderProgress {
	total: number;
	transferred: number;
	processed: number;
	failed: number;
}

export function runProgress(schedule: Schedule): FolderProgress | null {
	const run = schedule.last_run;
	if (!runIsLive(run)) return null;
	const counts = run?.counts ?? {};
	if (typeof counts.planned !== 'number' || counts.planned <= 0) return null;
	return {
		total: counts.planned,
		transferred: Math.min(counts.fetched ?? 0, counts.planned),
		processed: Math.min(counts.landed ?? 0, counts.planned),
		failed: counts.failed ?? 0
	};
}

const SKIP_REASONS: Record<string, { label: string; explainer: string }> = {
	unsupported_content_type: {
		label: 'File type not supported',
		explainer:
			'Only PDF, Word, PowerPoint, Excel, CSV, HTML, Markdown, XML and plain-text files are synced. Save the file in one of these formats to include it.'
	},
	item_too_large: {
		label: 'Too large',
		explainer: 'The file is larger than the sync limit allows.'
	},
	empty_content: {
		label: 'Empty document: no text found',
		explainer: 'No text could be extracted, for example a scanned image without OCR.'
	},
	empty_parsed_content: {
		label: 'Empty document: no text found',
		explainer: 'No text could be extracted, for example a scanned image without OCR.'
	},
	processing_failed: {
		label: 'Processing failed',
		explainer: 'The file could not be processed. It is retried on the next sync.'
	},
	timed_out: {
		label: 'Processing timed out',
		explainer: 'Processing did not finish in time. It is retried on the next sync.'
	},
	restricted_item: {
		label: 'Protected file',
		explainer: 'The file is protected in {{provider}} and cannot be read.'
	},
	access_revoked: {
		label: 'No access',
		explainer: 'You no longer have access to this file in {{provider}}.'
	},
	not_landed: {
		label: 'Not synced yet',
		explainer: 'The file has not been synced yet, so its permissions could not be updated.'
	}
};
const SKIP_FALLBACK = {
	label: 'Internal error',
	explainer: 'The file was skipped in the last sync. It is retried on the next sync.'
};

export function skippedReason(code: string): string {
	return SKIP_REASONS[code]?.label ?? (code in KNOWN_INTERNAL ? SKIP_FALLBACK.label : code);
}

// Why a file was skipped, in one sentence the user can act on; `{{provider}}`
// is left for the caller to fill.
export function skippedExplainer(code: string): string {
	return (SKIP_REASONS[code] ?? SKIP_FALLBACK).explainer;
}

// Worker-internal reasons (sync_execution.py) that mean nothing to the user.
const KNOWN_INTERNAL: Record<string, true> = {
	internal_error: true,
	acl_write_failed: true,
	acl_read_failed: true,
	source_failed: true,
	reach_failed: true,
	delete_failed: true,
	blank_failed: true,
	unresolvable_principal: true
};

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
