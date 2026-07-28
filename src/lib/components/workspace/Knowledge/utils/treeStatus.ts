/**
 * Status helpers for the KB directory browser.
 *
 * The files endpoint annotates each directory with a bucketed `status_counts`
 * rollup over its recursive descendants; these helpers turn that rollup into
 * a single per-folder badge state without re-deriving it in the template.
 */

export interface TreeStatusCounts {
	pending?: number;
	completed?: number;
	failed?: number;
	unknown?: number;
}

/**
 * Folder breadcrumb segments for a search hit, derived from its
 * provider-relative path (which ends in the filename). The last segment (the
 * filename) is dropped, so `"docs/sub/file.pdf"` → `["docs", "sub"]` and a
 * root-level file → `[]`.
 */
export function breadcrumbSegments(relativePath: string | null | undefined): string[] {
	if (!relativePath) return [];
	const parts = relativePath.split('/');
	parts.pop(); // drop the filename
	return parts.filter((p) => p.length > 0);
}

export type FolderBadge = 'failed' | 'pending' | 'none';

/**
 * Reduce a folder's `status_counts` rollup to a single badge:
 *   - `failed`  when any descendant failed (takes precedence — surfaces errors),
 *   - `pending` when any descendant is still in progress,
 *   - `none`    otherwise (all completed / unknown).
 */
export function folderBadge(counts: TreeStatusCounts | null | undefined): FolderBadge {
	if (!counts) return 'none';
	if ((counts.failed ?? 0) > 0) return 'failed';
	if ((counts.pending ?? 0) > 0) return 'pending';
	return 'none';
}

/**
 * Per-file status normalised to the same render states the existing tree uses
 * (spinner while in-progress, warning on error, document icon otherwise).
 */
export type FileBadge = 'spinner' | 'error' | 'idle';

const IN_PROGRESS = new Set(['uploading', 'pending', 'downloading', 'parsing', 'ingesting']);
const ERRORED = new Set(['error', 'cancelled', 'failed']);

export function fileBadge(status: string | null | undefined): FileBadge {
	if (status && IN_PROGRESS.has(status)) return 'spinner';
	if (status && ERRORED.has(status)) return 'error';
	return 'idle';
}
