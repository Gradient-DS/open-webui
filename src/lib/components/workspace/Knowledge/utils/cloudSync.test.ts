import { describe, expect, it } from 'vitest';
import type { Connection, Schedule } from '$lib/apis/cloudSync';
import {
	connectionOutcome,
	pairSchedules,
	connectResult,
	googleDriveScope,
	oneDriveScope,
	reconnectConnections,
	runCounts,
	runIsLive,
	runStatus
} from './cloudSync';

it('registers OneDrive folders recursively and files as single-file scopes', () => {
	expect(oneDriveScope({ id: 'folder', driveId: 'drive', type: 'folder' })).toEqual({
		drive_id: 'drive',
		item_id: 'folder',
		include_descendants: true,
		single_file: false
	});
	expect(oneDriveScope({ id: 'file', driveId: 'drive', type: 'file' })).toEqual({
		drive_id: 'drive',
		item_id: 'file',
		include_descendants: false,
		single_file: true
	});
});

it('registers Google folders and files without carrying picker credentials', () => {
	expect(googleDriveScope({ id: 'folder', type: 'folder' })).toEqual({
		file_id: 'folder',
		drive_id: null,
		include_descendants: true
	});
	expect(googleDriveScope({ id: 'file', type: 'file' }).include_descendants).toBe(false);
});

describe('connect popup messages', () => {
	const popup = {} as Window;
	const origin = 'https://owui.invalid';
	const event = {
		origin,
		source: popup,
		data: { type: 'soev_connect', connection: 'c', result: 'pending' }
	};
	it('accepts the expected callback without treating pending as enabled', () => {
		expect(connectResult(event, origin, popup, 'c')).toBe('pending');
	});
	it('ignores other origins, windows, connections, message types and results', () => {
		for (const invalid of [
			{ ...event, origin: 'https://other.invalid' },
			{ ...event, source: {} as Window },
			{ ...event, data: { ...event.data, connection: 'other' } },
			{ ...event, data: { ...event.data, type: 'onedrive_auth_callback' } },
			{ ...event, data: { ...event.data, result: 'success' } }
		])
			expect(connectResult(invalid, origin, popup, 'c')).toBeNull();
		expect(connectResult(event, origin, popup)).toBeNull();
	});
	it.each(['error', 'invalid'])('surfaces a %s callback', (result) => {
		expect(connectResult({ ...event, data: { ...event.data, result } }, origin, popup, 'c')).toBe(
			result
		);
	});
});

it('deduplicates reconnect banners and trusts polled lifecycle over a stale popup state', () => {
	const connection: Connection = {
		id: 'c',
		source_kind: 'onedrive',
		lifecycle: 'suspended:reauth'
	};
	const schedule = { connection } as Schedule;
	expect(reconnectConnections([schedule, schedule], null)).toEqual([connection]);
	const enabled = { ...connection, lifecycle: 'enabled' };
	expect(reconnectConnections([{ connection: enabled } as Schedule], connection)).toEqual([]);
	expect(reconnectConnections([], { ...connection, lifecycle: 'pending' })).toHaveLength(1);
	expect(reconnectConnections([], { ...connection, lifecycle: 'revoked' })).toEqual([]);
});

// The payload below is a verbatim soev-api StoredRun, not an invented shape:
// counts are nested, and `secret_days` rides in counts as an expiry warning.
it('reads counters out of the nested counts map and leaves secret_days out', () => {
	expect(
		runCounts({
			id: 'run',
			started_at: '2026-09-17T12:30:46Z',
			finished_at: '2026-09-17T12:30:56Z',
			outcome: 'succeeded',
			counts: {
				fetched: 1,
				submitted: 1,
				landed: 1,
				unchanged: 0,
				deleted: 0,
				failed: 0,
				timed_out: 0,
				secret_days: 90
			}
		})
	).toEqual([
		{ label: 'Synced', count: 1 },
		{ label: 'Fetched', count: 1 },
		{ label: 'Submitted', count: 1 },
		{ label: 'Unchanged', count: 0 },
		{ label: 'Deleted', count: 0 },
		{ label: 'Failed', count: 0 },
		{ label: 'Timed out', count: 0 }
	]);
	expect(runCounts({ id: 'run', started_at: '2026-09-17T12:30:46Z' })).toEqual([]);
});

it('derives a run status from outcome, because soev-api sends no status field', () => {
	const started = { id: 'run', started_at: '2026-09-17T12:30:46Z' };
	expect(runStatus(started)).toBe('running');
	expect(runIsLive(started)).toBe(true);
	expect(runStatus({ ...started, cancel_requested_at: '2026-09-17T12:30:50Z' })).toBe('cancelling');
	expect(runStatus({ ...started, outcome: 'partial' })).toBe('partial');
	// A cancel that landed is terminal: the outcome wins over the request stamp.
	expect(
		runStatus({ ...started, outcome: 'cancelled', cancel_requested_at: '2026-09-17T12:30:50Z' })
	).toBe('cancelled');
	expect(runIsLive({ ...started, outcome: 'succeeded' })).toBe(false);
	expect(runIsLive(null)).toBe(false);
});

const scheduleFixture = (
	id: string,
	kind: Schedule['kind'],
	scope = { item_id: 'folder' }
): Schedule => ({
	id,
	kind,
	scope,
	connection_id: 'c',
	subscribers: ['kb'],
	subscriber_count: 1,
	cadence_minutes: 60,
	source_kind: 'onedrive',
	lifecycle: 'enabled',
	connection: { id: 'c', source_kind: 'onedrive', lifecycle: 'enabled' }
});

it('pairs schedules by connection and deep-equal scope regardless of key or schedule order', () => {
	const content = {
		...scheduleFixture('content', 'content'),
		scope: { item_id: 'folder', single_file: false }
	};
	const acl = {
		...scheduleFixture('acl', 'acl_refresh'),
		scope: { single_file: false, item_id: 'folder' }
	};
	const otherConnection = { ...acl, id: 'other', connection_id: 'other' };
	const loneContent = scheduleFixture('lone', 'content', { item_id: 'different' });
	expect(pairSchedules([otherConnection, acl, content, loneContent])).toEqual([
		{ content, acl },
		{ content: loneContent, acl: undefined },
		{ acl: otherConnection }
	]);
	expect(pairSchedules([])).toEqual([]);
	expect(
		pairSchedules([content, { ...content, id: 'duplicate' }, acl]).filter((pair) => pair.acl)
	).toHaveLength(1);
});

it.each([
	['enabled', null, false, 0, { status: 'done' }],
	['enabled', 'invalid_grant', false, 0, { status: 'failed', reason: 'invalid_grant' }],
	['pending', 'consent_denied', false, 0, { status: 'failed', reason: 'consent_denied' }],
	['suspended:reauth', null, false, 0, { status: 'failed', reason: 'suspended:reauth' }],
	['revoked', null, true, 2, { status: 'failed', reason: 'revoked' }],
	['pending', null, false, 0, { status: 'waiting' }],
	['pending', null, true, 1, { status: 'waiting' }],
	['pending', null, true, 2, { status: 'gave_up' }],
	['enabled', null, true, 2, { status: 'done' }]
] as const)(
	'resolves connection %s with error %s, popup closed %s, check %i',
	(lifecycle, last_error, closed, checks, expected) => {
		expect(
			connectionOutcome({ id: 'c', source_kind: 'onedrive', lifecycle, last_error }, closed, checks)
		).toEqual(expected);
	}
);

it('keeps an owner mismatch visible even when the connection lifecycle is enabled', () => {
	const connection: Connection = {
		id: 'c',
		source_kind: 'google_drive',
		lifecycle: 'enabled',
		last_error: 'owner_mismatch'
	};
	expect(reconnectConnections([{ connection } as Schedule], null)).toEqual([connection]);
});
