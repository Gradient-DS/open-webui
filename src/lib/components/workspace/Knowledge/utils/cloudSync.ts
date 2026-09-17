import type { Connection, Schedule, ScheduleForm, SyncRun } from '$lib/apis/cloudSync';

export function oneDriveScope(item: {
	id: string;
	driveId: string;
	type: 'file' | 'folder';
}): ScheduleForm['scope'] {
	return {
		drive_id: item.driveId,
		item_id: item.id,
		include_descendants: item.type === 'folder',
		single_file: item.type === 'file'
	};
}

export function googleDriveScope(item: {
	id: string;
	type: 'file' | 'folder';
}): ScheduleForm['scope'] {
	return { file_id: item.id, drive_id: null, include_descendants: item.type === 'folder' };
}

export function connectResult(
	event: Pick<MessageEvent, 'origin' | 'source' | 'data'>,
	origin: string,
	popup: Window,
	connectionId?: string
): 'pending' | 'error' | 'invalid' | null {
	if (
		!connectionId ||
		event.origin !== origin ||
		event.source !== popup ||
		event.data?.type !== 'soev_connect' ||
		event.data.connection !== connectionId
	)
		return null;
	const result = event.data.result;
	return result === 'pending' || result === 'error' || result === 'invalid' ? result : null;
}

export function reconnectConnections(
	schedules: Schedule[],
	pending: Connection | null
): Connection[] {
	const connections = new Map(
		schedules.map((schedule) => [schedule.connection.id, schedule.connection])
	);
	if (pending && !connections.has(pending.id)) connections.set(pending.id, pending);
	return [...connections.values()].filter(
		(connection) =>
			['onedrive', 'google_drive'].includes(connection.source_kind) &&
			['pending', 'suspended:reauth'].includes(connection.lifecycle)
	);
}

export function runCounts(run: SyncRun): { label: string; count: number }[] {
	const labels = {
		observed: 'Observed',
		landed: 'Synced',
		added: 'Added',
		updated: 'Updated',
		unchanged: 'Unchanged',
		deleted: 'Deleted',
		removed: 'Removed',
		failed: 'Failed',
		refused: 'Refused'
	};
	return Object.entries(labels).flatMap(([field, label]) => {
		const count = run[field];
		return typeof count === 'number' && Number.isFinite(count) ? [{ label, count }] : [];
	});
}
