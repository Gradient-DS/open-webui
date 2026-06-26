/**
 * Helpers for large-tree rendering decisions in cloud-KB file lists.
 */

/**
 * Default threshold: trees with more than this many files are considered
 * "large" and folder sources are left collapsed (collapsed-by-default).
 * Below this count, all top-level folder sources auto-expand so that
 * small KBs feel immediately usable.
 */
export const LARGE_TREE_THRESHOLD = 50;

/**
 * Returns true when the total file count indicates a "large" tree where
 * folder sources should be collapsed by default.
 *
 * @param totalFiles - total file count for the KB, or null when unknown
 * @param threshold  - the cutoff (exclusive); defaults to LARGE_TREE_THRESHOLD
 */
export function isLargeTree(
	totalFiles: number | null,
	threshold: number = LARGE_TREE_THRESHOLD
): boolean {
	if (totalFiles === null) return false;
	return totalFiles >= threshold;
}

/**
 * Computes the initial expansion map for a set of source item IDs.
 * Small trees: all sources expanded. Large trees: all sources collapsed.
 *
 * @param sourceItemIds - the item_id values of all folder sources
 * @param totalFiles    - total file count for the KB, or null when unknown
 * @param threshold     - the large-tree cutoff (defaults to LARGE_TREE_THRESHOLD)
 */
export function computeInitialExpansion(
	sourceItemIds: string[],
	totalFiles: number | null,
	threshold: number = LARGE_TREE_THRESHOLD
): Record<string, boolean> {
	const large = isLargeTree(totalFiles, threshold);
	const result: Record<string, boolean> = {};
	for (const id of sourceItemIds) {
		result[id] = !large;
	}
	return result;
}
