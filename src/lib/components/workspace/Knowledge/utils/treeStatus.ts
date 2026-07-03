/**
 * Helpers for the lazy per-folder KB tree browser (Tier 2).
 *
 * The backend `/knowledge/{id}/tree` endpoint returns folders with a bucketed
 * `status_counts` rollup over their recursive descendants. These helpers turn
 * that rollup into a single per-folder badge state without re-deriving it in
 * the template.
 */

export interface TreeStatusCounts {
	pending?: number;
	completed?: number;
	failed?: number;
	unknown?: number;
}

export interface TreeFolder {
	name: string;
	path: string;
	child_count: number;
	status_counts: TreeStatusCounts;
	type?: string | null;
}

export interface TreeFile {
	id: string;
	name: string;
	size?: number | null;
	status?: string | null;
	error?: string | null;
	updated_at?: number | null;
}

export interface TreeLevel {
	folders: TreeFolder[];
	files: TreeFile[];
	next_cursor: string | null;
	has_more: boolean;
}

export interface SearchHit extends TreeFile {
	relative_path?: string | null;
	source_item_id?: string | null;
}

export interface SearchLevel {
	items: SearchHit[];
	next_cursor: string | null;
	has_more: boolean;
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
