import { describe, expect, it } from 'vitest';
import type { Connection, Schedule } from '$lib/apis/cloudSync';
import {
	connectResult,
	googleDriveScope,
	oneDriveScope,
	reconnectConnections,
	runCounts
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

it('shows supplied run counters without inventing a total or daemon-stage progress', () => {
	expect(
		runCounts({
			id: 'run',
			status: 'running',
			observed: 5,
			landed: 3,
			failed: 1,
			started_at: 42,
			stage_counts: { ok: 99 }
		})
	).toEqual([
		{ label: 'Observed', count: 5 },
		{ label: 'Synced', count: 3 },
		{ label: 'Failed', count: 1 }
	]);
	expect(runCounts({ id: 'run', status: 'queued' })).toEqual([]);
});
