import { describe, expect, it } from 'vitest';
import type { Connection, Schedule, SyncRun } from '$lib/apis/cloudSync';
import {
	sourceState,
	sourceTiming,
	skippedReason,
	skippedRunKey,
	skippedExplainer,
	runProgress
} from './sourceState';

const connection: Connection = { id: 'c', source_kind: 'onedrive', lifecycle: 'enabled' };
const run: SyncRun = {
	id: 'r',
	started_at: '2026-09-18T10:00:00Z',
	finished_at: '2026-09-18T10:01:00Z',
	outcome: 'succeeded'
};
const schedule = (changes: Partial<Schedule> = {}): Schedule => ({
	id: 's',
	connection_id: 'c',
	connection,
	kind: 'content',
	source_kind: 'onedrive',
	lifecycle: 'enabled',
	scope: { include_descendants: true },
	subscribers: ['kb'],
	subscriber_count: 1,
	document_count: 1,
	...changes
});
const view = (changes: Partial<Schedule> = {}, account = connection) =>
	sourceState({ content: schedule(changes) }, account);

describe('skipped run keys', () => {
	it('does not request skipped items during a live or cancelling run', () => {
		for (const cancel_requested_at of [null, '2026-09-18T10:00:30Z']) {
			expect(
				skippedRunKey(schedule({ last_run: { ...run, outcome: null, cancel_requested_at } }))
			).toBe('');
		}
		expect(skippedRunKey(undefined)).toBe('');
	});
	it('keeps a finished run key stable across polls and distinguishes sources and runs', () => {
		const key = skippedRunKey(schedule({ last_run: run }));
		expect(key).toBe(JSON.stringify(['s', run.id, run.finished_at]));
		expect(
			skippedRunKey(schedule({ last_run: { ...run, counts: { failed: 2 } }, document_count: 5 }))
		).toBe(key);
		expect(skippedRunKey(schedule({ id: 'other', last_run: run }))).not.toBe(key);
		expect(skippedRunKey(schedule({ last_run: { ...run, id: 'next' } }))).not.toBe(key);
	});
});

describe('source states', () => {
	it('syncing', () => {
		expect(view({ last_run: { ...run, outcome: null } })).toMatchObject({
			state: 'syncing',
			primary: null
		});
	});
	it('needs_reconnect', () => {
		for (const lifecycle of ['pending', 'suspended:reauth', 'revoked'])
			expect(view({}, { ...connection, lifecycle })).toMatchObject({
				state: 'needs_reconnect',
				primary: 'reconnect'
			});
		for (const last_error of ['access_revoked', 'credential_unusable', 'owner_mismatch'])
			expect(view({ last_error }).state).toBe('needs_reconnect');
	});
	it('needs reconnect for a provider without a frontend adapter', () => {
		const account = { ...connection, source_kind: 'confluence', last_error: 'owner_mismatch' };
		expect(view({ source_kind: 'confluence', connection: account }, account)).toMatchObject({
			state: 'needs_reconnect',
			primary: 'reconnect',
			provider: 'confluence'
		});
	});
	it('needs_access', () => {
		expect(view({ last_error: 'writer_revoked' })).toMatchObject({
			state: 'needs_access',
			primary: 'request_access'
		});
	});
	it('paused', () => {
		expect(view({ lifecycle: 'suspended', last_error: null })).toMatchObject({
			state: 'paused',
			primary: 'resume'
		});
	});
	it('error', () => {
		expect(view({ last_run: { ...run, outcome: 'failed' } })).toMatchObject({
			state: 'error',
			primary: 'sync_now'
		});
	});
	it('partly_synced', () => {
		expect(view({ last_run: { ...run, outcome: 'partial' } }).state).toBe('partly_synced');
		expect(view({ last_run: { ...run, counts: { failed: 1 } } }).state).toBe('partly_synced');
	});
	it('up_to_date', () => {
		expect(view({ last_run: run })).toMatchObject({ state: 'up_to_date', primary: 'sync_now' });
	});
	it('scheduled', () => {
		expect(view()).toMatchObject({ state: 'scheduled', primary: 'sync_now', lastSyncedAt: null });
	});
});

it('applies precedence across both schedules and the connection', () => {
	const levels: Partial<Schedule>[] = [
		{ last_run: { ...run, outcome: null } },
		{ last_error: 'access_revoked' },
		{ last_error: 'writer_revoked' },
		{ lifecycle: 'suspended', last_error: null },
		{ last_run: { ...run, outcome: 'failed' } },
		{ last_run: { ...run, outcome: 'partial' } },
		{ last_run: run },
		{}
	];
	const states = [
		'syncing',
		'needs_reconnect',
		'needs_access',
		'paused',
		'error',
		'partly_synced',
		'up_to_date',
		'scheduled'
	];
	for (let high = 0; high < levels.length; high++) {
		for (let low = high + 1; low < levels.length; low++) {
			expect(
				sourceState({ content: schedule(levels[high]), acl: schedule(levels[low]) }, connection)
					.state
			).toBe(states[high]);
			if (high < 6)
				expect(
					sourceState({ content: schedule(levels[low]), acl: schedule(levels[high]) }, connection)
						.state
				).toBe(states[high]);
		}
	}
	expect(view(levels[0], { ...connection, lifecycle: 'pending' }).state).toBe('syncing');
	expect(view(levels[2], { ...connection, lifecycle: 'pending' }).state).toBe('needs_reconnect');
});

it('uses current schedule errors before stale run errors', () => {
	expect(
		view({
			last_error: 'writer_revoked',
			last_run: { ...run, outcome: 'failed', error_code: 'access_revoked' }
		}).state
	).toBe('needs_access');
});

it('keeps source metadata, timestamps and counts skipped files from only the content run', () => {
	const content = schedule({
		label: 'Reports',
		document_count: 12,
		path: '/Team/Reports',
		next_due_at: '2026-09-18T11:00:00Z',
		last_run: {
			...run,
			counts: { landed: 12, failed: 3, item_too_large: 2, link_grants_dropped: 2 }
		}
	});
	const acl = schedule({
		kind: 'acl_refresh',
		last_run: { ...run, counts: { landed: 99, failed: 2, link_grants_dropped: 3 } }
	});
	expect(sourceState({ content, acl }, connection)).toMatchObject({
		label: 'Reports',
		path: '/Team/Reports',
		provider: 'OneDrive',
		documents: 12,
		skipped: 3,
		lastSyncedAt: run.finished_at,
		nextDueAt: content.next_due_at
	});
	expect(view().skipped).toBe(0);
	expect(sourceState({ acl }, connection).skipped).toBe(0);
	expect(sourceState({ content: schedule(), acl }, connection).skipped).toBe(0);
});

it('counts other knowledge bases without adding paired subscriber counts', () => {
	const content = schedule({ subscriber_count: 3 });
	const acl = schedule({ kind: 'acl_refresh', subscriber_count: 2 });
	expect(sourceState({ content, acl }, connection).otherKbs).toBe(2);
	expect(sourceState({ acl }, connection).otherKbs).toBe(1);
	expect(view({ subscriber_count: undefined }).otherKbs).toBe(0);
	expect(view({ subscriber_count: 0 }).otherKbs).toBe(0);
});

it('falls back to file and folder labels for legacy and ACL-only sources', () => {
	expect(view().label).toBe('Folder');
	expect(view({ scope: { single_file: true } }).label).toBe('File');
	expect(view({ scope: { include_descendants: false } }).label).toBe('File');
	expect(
		sourceState({ acl: schedule({ kind: 'acl_refresh', document_count: 0 }) }, connection)
	).toMatchObject({
		label: 'Folder',
		primary: null,
		documents: 0
	});
});

it('fails when no documents exist, using reach before the last run landed count', () => {
	const last_run = { ...run, outcome: 'partial' as const, counts: { failed: 2, landed: 0 } };
	expect(view({ document_count: 0, last_run }).state).toBe('error');
	expect(view({ document_count: 3, last_run }).state).toBe('partly_synced');
	expect(view({ document_count: undefined, last_run }).state).toBe('error');
	expect(
		view({ document_count: undefined, last_run: { ...last_run, counts: { failed: 2, landed: 1 } } })
			.state
	).toBe('partly_synced');
	expect(
		view({ document_count: 0, last_run: { ...last_run, counts: { failed: 2, landed: 1 } } }).state
	).toBe('error');
});

it.each([
	['unsupported_content_type', 'File type not supported'],
	['item_too_large', 'Too large'],
	['empty_content', 'Empty document: no text found'],
	['processing_failed', 'Processing failed'],
	['timed_out', 'Processing timed out'],
	['acl_write_failed', 'Internal error'],
	['internal_error', 'Internal error'],
	['access_revoked', 'No access'],
	['new_code', 'new_code']
])('names skipped reason %s', (code, reason) => expect(skippedReason(code)).toBe(reason));

it('shows the current reach instead of the last run landed count', () => {
	const last_run = { ...run, counts: { landed: 0, unchanged: 12 } };
	expect(view({ document_count: 12, last_run }).documents).toBe(12);
	expect(view({ document_count: 0, last_run: { ...run, counts: { landed: 9 } } }).documents).toBe(
		0
	);
	expect(
		view({ document_count: undefined, last_run: { ...run, counts: { landed: 9 } } }).documents
	).toBe(9);
	expect(view({ document_count: undefined }).documents).toBe(0);
});

describe('source timing copy', () => {
	const now = Date.parse('2026-09-19T10:00:00Z');
	it('omits the last synced label before the first sync', () => {
		expect(
			sourceTiming({ lastSyncedAt: null, nextDueAt: null, documents: 0 }, () => 'relative', now)
		).toEqual({
			lastSync: { key: 'Not synced yet · {{count}} documents', values: { count: 0 } },
			nextCheck: null
		});
	});
	it.each(['2026-09-19T09:59:59Z', '2026-09-19T10:00:00Z'])(
		'shows overdue checks as soon as possible: %s',
		(nextDueAt) => {
			expect(
				sourceTiming(
					{ lastSyncedAt: null, nextDueAt, documents: 3 },
					() => 'a few seconds ago',
					now
				).nextCheck
			).toEqual({ key: 'Next check: as soon as possible', values: {} });
		}
	);
	it('keeps relative times for completed syncs and future checks', () => {
		const lastSyncedAt = '2026-09-19T09:00:00Z';
		const nextDueAt = '2026-09-19T11:00:00Z';
		expect(
			sourceTiming(
				{ lastSyncedAt, nextDueAt, documents: 12 },
				(time) => (time === lastSyncedAt ? 'an hour ago' : 'in an hour'),
				now
			)
		).toEqual({
			lastSync: {
				key: 'Last synced {{time}} · {{count}} documents',
				values: { time: 'an hour ago', count: 12 }
			},
			nextCheck: { key: 'Next check {{time}}', values: { time: 'in an hour' } }
		});
	});
});

it('shows documents so far while the first run lands and never calls landed documents unsynced', () => {
	const timing = (
		state: 'syncing' | 'scheduled',
		documents: number,
		lastSyncedAt: string | null = null
	) => sourceTiming({ state, documents, lastSyncedAt, nextDueAt: null }, () => 'relative');
	expect(timing('syncing', 61).lastSync).toEqual({
		key: '{{count}} documents so far',
		values: { count: 61 }
	});
	expect(timing('syncing', 0).lastSync.key).toBe('{{count}} documents so far');
	expect(timing('syncing', 61, '2026-09-19T10:00:00Z').lastSync.key).toBe(
		'{{count}} documents so far'
	);
	expect(timing('scheduled', 61).lastSync.key).toBe('{{count}} documents');
	expect(timing('scheduled', 0).lastSync.key).toBe('Not synced yet · {{count}} documents');
});

describe('run progress', () => {
	it('is absent outside a live run', () => {
		expect(runProgress(schedule({ last_run: run }))).toBeNull();
		expect(runProgress(schedule({ last_run: null }))).toBeNull();
	});
	it('is absent while the worker publishes no planned work', () => {
		expect(
			runProgress(schedule({ document_count: 7, last_run: { ...run, outcome: null } }))
		).toBeNull();
	});
	it('reads fetched and landed of planned once a live run carries counts', () => {
		expect(
			runProgress(
				schedule({
					last_run: { ...run, outcome: null, counts: { planned: 40, fetched: 12, landed: 3 } }
				})
			)
		).toEqual({ total: 40, transferred: 12, processed: 3, failed: 0 });
	});
	it('includes failures and caps transfer and processing counts at planned work', () => {
		expect(
			runProgress(
				schedule({
					last_run: {
						...run,
						outcome: null,
						counts: { planned: 5, fetched: 6, landed: 7, failed: 2 }
					}
				})
			)
		).toEqual({ total: 5, transferred: 5, processed: 5, failed: 2 });
	});
});

describe('skip explainers', () => {
	it('name the specific reason and keep worker-internal codes generic', () => {
		expect(skippedReason('timed_out')).toBe('Processing timed out');
		expect(skippedReason('empty_parsed_content')).toBe('Empty document: no text found');
		expect(skippedReason('reach_failed')).toBe('Internal error');
		expect(skippedReason('something_new')).toBe('something_new');
	});
	it('explain what the user can do about it', () => {
		expect(skippedExplainer('unsupported_content_type')).toMatch(/PDF, Word/);
		expect(skippedExplainer('restricted_item')).toContain('{{provider}}');
		expect(skippedExplainer('reach_failed')).toMatch(/retried/);
	});
});
