import { describe, expect, it } from 'vitest';
import type { Connection, Schedule } from '$lib/apis/cloudSync';
import type { SchedulePair } from '../utils/cloudSync';
import { removalTargets, sortSourcePairs, sourcePairItem } from './sources';

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
const nebul = pair('n', 'nebul.docx', synced('2026-10-03T10:00:00Z'));
const never = pair('x', 'Agenda.docx');
const labels = (pairs: SchedulePair[]) => pairs.map((item) => item.content!.label);

describe('sortSourcePairs', () => {
	it('orders by label case-insensitively by default and for name', () => {
		expect(labels(sortSourcePairs([provider, nebul, never], null, null))).toEqual([
			'Agenda.docx',
			'nebul.docx',
			'Provider.docx'
		]);
		expect(labels(sortSourcePairs([provider, nebul, never], 'name', 'asc'))).toEqual([
			'Agenda.docx',
			'nebul.docx',
			'Provider.docx'
		]);
	});

	it('treats a key without direction as descending', () => {
		expect(labels(sortSourcePairs([never, nebul, provider], 'name', null))).toEqual([
			'Provider.docx',
			'nebul.docx',
			'Agenda.docx'
		]);
	});

	it('orders by last sync under updated, never-synced as oldest', () => {
		expect(labels(sortSourcePairs([never, nebul, provider], 'updated_at', null))).toEqual([
			'nebul.docx',
			'Provider.docx',
			'Agenda.docx'
		]);
		expect(labels(sortSourcePairs([nebul, provider, never], 'updated_at', 'asc'))).toEqual([
			'Agenda.docx',
			'Provider.docx',
			'nebul.docx'
		]);
	});

	it('keeps label order under created, which schedules do not carry', () => {
		expect(labels(sortSourcePairs([provider, never, nebul], 'created_at', null))).toEqual([
			'Agenda.docx',
			'nebul.docx',
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
