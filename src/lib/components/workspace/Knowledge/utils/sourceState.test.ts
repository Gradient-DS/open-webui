import { describe, expect, it } from 'vitest';
import type { Connection, Schedule, SyncRun } from '$lib/apis/cloudSync';
import { sourceState, skippedReason } from './sourceState';

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

it('keeps source metadata, timestamps and counts skipped files across both runs', () => {
	const content = schedule({
		label: 'Reports',
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
		skipped: 5,
		lastSyncedAt: run.finished_at,
		nextDueAt: content.next_due_at
	});
	expect(view().skipped).toBe(0);
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
	expect(sourceState({ acl: schedule({ kind: 'acl_refresh' }) }, connection)).toMatchObject({
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
	['acl_write_failed', 'Internal error'],
	['internal_error', 'Internal error'],
	['access_revoked', 'No access'],
	['new_code', 'new_code']
])('names skipped reason %s', (code, reason) => expect(skippedReason(code)).toBe(reason));
