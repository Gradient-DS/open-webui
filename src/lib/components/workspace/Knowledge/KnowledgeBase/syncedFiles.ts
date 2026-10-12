import type { SchedulePair } from '../utils/cloudSync';

type ListedFile = { meta?: { soev_schedule_ids?: string[] } };

// [Gradient] A synced file belongs to its schedules and leaves with its source.
export const isSynced = (file: ListedFile | null | undefined): boolean =>
	(file?.meta?.soev_schedule_ids ?? []).length > 0;

// A single-file source's own row already stands for its file, so that file is
// not listed a second time; files any other schedule reaches stay listed.
export const withoutLooseSourceFiles = <T extends ListedFile>(
	files: T[],
	looseSources: SchedulePair[]
): T[] => {
	const loose = new Set(
		looseSources.flatMap((pair) => [pair.content?.id, pair.acl?.id]).filter((id) => !!id)
	);
	return files.filter(
		(file) => !isSynced(file) || !file.meta!.soev_schedule_ids!.every((id) => loose.has(id))
	);
};
