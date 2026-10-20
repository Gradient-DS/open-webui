import type { Schedule } from '$lib/apis/cloudSync';
import type { SourceProvider } from '$lib/sources/registry';
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

export type SourceMenuAction =
	| 'copy_path'
	| 'retry_failed'
	| 'cancel'
	| 'pause'
	| 'resume'
	| 'remove';

// The ⋯ menu of a source, folder or single file alike. Remove unsubscribes
// this KB, which soev-api grants any KB writer; pause and resume act on the
// connection owner's schedule and stay with admins. Retry failed files is a
// writer's run, offered while the last run left files behind.
export const sourceMenu = (source: {
	itemPath: string;
	writeAccess: boolean;
	isAdmin: boolean;
	live: boolean;
	lifecycle: string;
	skipped?: number;
}): SourceMenuAction[] => {
	const actions: SourceMenuAction[] = [];
	if (source.itemPath) actions.push('copy_path');
	if (source.writeAccess && !source.live && (source.skipped ?? 0) > 0) actions.push('retry_failed');
	if (source.writeAccess && source.live) actions.push('cancel');
	if (source.isAdmin && source.lifecycle === 'enabled') actions.push('pause');
	if (source.isAdmin && source.lifecycle === 'suspended') actions.push('resume');
	if (source.writeAccess) actions.push('remove');
	return actions;
};

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

// A new cloud knowledge base opens its provider's picker by itself (the create
// page asks for it in the URL), but only where nothing needs a click: a picker
// that can open without a window, for a writer, while the KB has no source yet.
// The caller still needs an enabled connection, or consent would open a window.
export const autoPickProvider = (state: {
	requested: SourceProvider | null;
	writeAccess: boolean;
	providerEnabled: boolean;
	hasContentSource: boolean;
	statusKnown: boolean;
}): SourceProvider | null =>
	state.requested?.pickSilently &&
	state.writeAccess &&
	state.providerEnabled &&
	state.statusKnown &&
	!state.hasContentSource
		? state.requested
		: null;
