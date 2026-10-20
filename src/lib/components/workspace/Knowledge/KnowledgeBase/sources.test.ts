import { describe, expect, it } from 'vitest';
import type { Connection, Schedule } from '$lib/apis/cloudSync';
import type { SchedulePair } from '../utils/cloudSync';
import type { SourceProvider } from '$lib/sources/registry';
import {
	autoPickProvider,
	removalTargets,
	sortSourcePairs,
	sourceMenu,
	sourcePairItem
} from './sources';

const connection: Connection = { id: 'c', source_kind: 'onedrive', lifecycle: 'enabled' };
const schedule = (id: string, changes: Partial<Schedule> = {}): Schedule => ({
	id,
	connection_id: 'c',
	connection,
	kind: 'content',
	source_kind: 'onedrive',
	lifecycle: 'enabled',
	scope: { single_file: true },
	subscribers: ['kb'],
	subscriber_count: 1,
	document_count: 1,
	...changes
});
const synced = (at: string) => ({
	last_run: { id: 'r', started_at: at, finished_at: at, outcome: 'succeeded' as const }
});
const pair = (id: string, label: string, changes: Partial<Schedule> = {}): SchedulePair => ({
	content: schedule(id, { label, ...changes })
});

const provider = pair('p', 'Provider.docx', synced('2026-10-01T10:00:00Z'));
const notulen = pair('n', 'notulen.docx', synced('2026-10-03T10:00:00Z'));
const never = pair('x', 'Agenda.docx');
const labels = (pairs: SchedulePair[]) => pairs.map((item) => item.content!.label);

describe('sortSourcePairs', () => {
	it('orders by label case-insensitively by default and for name', () => {
		expect(labels(sortSourcePairs([provider, notulen, never], null, null))).toEqual([
			'Agenda.docx',
			'notulen.docx',
			'Provider.docx'
		]);
		expect(labels(sortSourcePairs([provider, notulen, never], 'name', 'asc'))).toEqual([
			'Agenda.docx',
			'notulen.docx',
			'Provider.docx'
		]);
	});

	it('treats a key without direction as descending', () => {
		expect(labels(sortSourcePairs([never, notulen, provider], 'name', null))).toEqual([
			'Provider.docx',
			'notulen.docx',
			'Agenda.docx'
		]);
	});

	it('orders by last sync under updated, never-synced as oldest', () => {
		expect(labels(sortSourcePairs([never, notulen, provider], 'updated_at', null))).toEqual([
			'notulen.docx',
			'Provider.docx',
			'Agenda.docx'
		]);
		expect(labels(sortSourcePairs([notulen, provider, never], 'updated_at', 'asc'))).toEqual([
			'Agenda.docx',
			'Provider.docx',
			'notulen.docx'
		]);
	});

	it('keeps label order under created, which schedules do not carry', () => {
		expect(labels(sortSourcePairs([provider, never, notulen], 'created_at', null))).toEqual([
			'Agenda.docx',
			'notulen.docx',
			'Provider.docx'
		]);
	});
});

describe('source selection and removal', () => {
	it('selects a source as one item keyed by its content schedule', () => {
		const item = sourcePairItem({
			content: schedule('c1', { label: 'Reports', document_count: 12 }),
			acl: schedule('a1', { kind: 'acl_refresh' })
		});
		expect(item).toMatchObject({ key: 'source:c1', kind: 'source', itemId: 'c1', fileCount: 12 });
	});

	it('removes the ACL schedule before the content schedule, like the source menu', () => {
		const content = schedule('c1');
		const acl = schedule('a1', { kind: 'acl_refresh' });
		expect(removalTargets({ content, acl }).map((item) => item.id)).toEqual(['a1', 'c1']);
		expect(removalTargets({ content }).map((item) => item.id)).toEqual(['c1']);
		expect(removalTargets({ acl }).map((item) => item.id)).toEqual(['a1']);
	});
});

describe('sourceMenu', () => {
	const base = { itemPath: '/Docs/a.docx', live: false, lifecycle: 'enabled' };

	it('offers Remove to a KB writer who is not an admin', () => {
		expect(sourceMenu({ ...base, writeAccess: true, isAdmin: false })).toEqual([
			'copy_path',
			'remove'
		]);
	});

	it('keeps Remove from a reader, admin or not', () => {
		expect(sourceMenu({ ...base, writeAccess: false, isAdmin: false })).toEqual(['copy_path']);
		expect(sourceMenu({ ...base, writeAccess: false, isAdmin: true })).toEqual([
			'copy_path',
			'pause'
		]);
	});

	it('gives admins pause or resume by lifecycle, and writers cancel while a run is live', () => {
		expect(sourceMenu({ ...base, writeAccess: true, isAdmin: true })).toEqual([
			'copy_path',
			'pause',
			'remove'
		]);
		expect(
			sourceMenu({
				itemPath: '',
				live: true,
				lifecycle: 'suspended',
				writeAccess: true,
				isAdmin: true
			})
		).toEqual(['cancel', 'resume', 'remove']);
	});

	it('offers Retry failed files to a writer while the last run left files behind', () => {
		expect(sourceMenu({ ...base, writeAccess: true, isAdmin: false, skipped: 3 })).toEqual([
			'copy_path',
			'retry_failed',
			'remove'
		]);
		expect(sourceMenu({ ...base, writeAccess: false, isAdmin: false, skipped: 3 })).toEqual([
			'copy_path'
		]);
		expect(
			sourceMenu({ ...base, live: true, writeAccess: true, isAdmin: false, skipped: 3 })
		).not.toContain('retry_failed');
		expect(sourceMenu({ ...base, writeAccess: true, isAdmin: false, skipped: 0 })).not.toContain(
			'retry_failed'
		);
	});
});

describe('autoPickProvider', () => {
	const silent = { kind: 'onedrive', pickSilently: async () => [] } as unknown as SourceProvider;
	const clickOnly = { kind: 'google_drive' } as unknown as SourceProvider;
	const ready = {
		requested: silent,
		writeAccess: true,
		providerEnabled: true,
		hasContentSource: false,
		statusKnown: true
	};

	it('opens the picker of a new knowledge base that asked for it', () => {
		expect(autoPickProvider(ready)).toBe(silent);
	});
	it.each([
		['nothing was asked', { requested: null }],
		['the picker needs a click', { requested: clickOnly }],
		['the reader cannot write', { writeAccess: false }],
		['the provider is off', { providerEnabled: false }],
		['a source exists already', { hasContentSource: true }],
		['the sync status is unknown', { statusKnown: false }]
	])('waits for a click when %s', (_, change) => {
		expect(autoPickProvider({ ...ready, ...change })).toBeNull();
	});
});
