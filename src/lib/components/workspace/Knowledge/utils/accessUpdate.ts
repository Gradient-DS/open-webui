export function accessUpdateState(meta: Record<string, unknown> | undefined) {
	const update = meta?.soev_access_update as { status?: string } | undefined;
	if (update?.status === 'QUEUED' || update?.status === 'RUNNING') return 'pending';
	if (['FAILED', 'CANCELLED', 'COMPLETED_WITH_ERRORS'].includes(update?.status ?? ''))
		return 'failed';
	return undefined;
}
