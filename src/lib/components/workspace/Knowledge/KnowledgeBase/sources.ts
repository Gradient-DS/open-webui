import type { Schedule } from '$lib/apis/cloudSync';
import type { SchedulePair } from '../utils/cloudSync';
import { sourceState } from '../utils/sourceState';
import { sourceItem } from './selection';

// [Gradient] Cloud sources in the KB listing: selection, removal and order.

const primary = (pair: SchedulePair): Schedule => (pair.content ?? pair.acl)!;

// A source is selected as one item, whatever schedules it pairs.
export const sourcePairItem = (pair: SchedulePair) =>
	sourceItem(primary(pair).id, primary(pair).label ?? '', primary(pair).document_count ?? 0);

// The schedules a remove deletes, ACL refresh before content.
export const removalTargets = (pair: SchedulePair): Schedule[] =>
	[pair.content, pair.acl].filter((item): item is Schedule => !!item).reverse();

const lastSynced = (pair: SchedulePair) => sourceState(pair, primary(pair).connection).lastSyncedAt;

const byLabel = (a: SchedulePair, b: SchedulePair) =>
	(primary(a).label ?? '').localeCompare(primary(b).label ?? '', undefined, {
		sensitivity: 'base',
		numeric: true
	}) || primary(a).id.localeCompare(primary(b).id);

// Loose sources follow the listing's sort like its files: by label, or by
// last sync under "Updated". Schedules carry no creation time, so "Created"
// keeps label order. A set key without direction means descending.
export const sortSourcePairs = (
	pairs: SchedulePair[],
	sortKey: string | null,
	direction: string | null
): SchedulePair[] => {
	const sign = sortKey && direction !== 'asc' ? -1 : 1;
	const rows = [...pairs].sort((a, b) => (sortKey === 'name' ? sign : 1) * byLabel(a, b));
	if (sortKey !== 'updated_at') return rows;
	// Never-synced sources sort as the oldest, like a missing timestamp server-side.
	const time = (pair: SchedulePair) => {
		const at = lastSynced(pair);
		return at ? Date.parse(at) : -Infinity;
	};
	return rows.sort((a, b) => sign * (time(a) - time(b)) || 0);
};
