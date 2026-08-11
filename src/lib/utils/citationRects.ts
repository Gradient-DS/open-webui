/**
 * Pure parser for coordinate-based citation highlights.
 *
 * Chunks ingested through warren's doc pipeline may carry `metadata.bboxes`:
 * a list of `{page?, x0, y0, x1, y1}` rects in PDF points, TOP-LEFT origin,
 * `page` 0-based (same convention as `metadata.page`). The Weaviate MT schema
 * stores the list as a JSON string (fixed schema, TEXT property), so a
 * JSON-string form is accepted alongside a real array.
 *
 * Framework-free (no Svelte, no pdf.js) so it can be unit-tested directly —
 * same rationale as `citationMatch.ts`.
 */

export interface HighlightRect {
	/** 1-indexed page number, ready for pdf.js. */
	page: number;
	x0: number;
	y0: number;
	x1: number;
	y1: number;
}

const isFiniteNumber = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v);

/**
 * Parse `metadata.bboxes` (array or JSON string) into viewer-ready rects.
 *
 * - Malformed input (bad JSON, non-array, entries missing numeric coords) is
 *   dropped; returns `null` when nothing valid remains so callers can fall
 *   back to text-match / page-jump.
 * - A rect without its own `page` inherits `metadata.page` (0-based), else 0.
 * - Pages are converted 0-based -> 1-based here; degenerate rects
 *   (x1 <= x0 or y1 <= y0) are dropped.
 */
export const rectsFromMetadata = (
	metadata: { bboxes?: unknown; page?: unknown } | null | undefined
): HighlightRect[] | null => {
	let bboxes = metadata?.bboxes;
	if (typeof bboxes === 'string') {
		try {
			bboxes = JSON.parse(bboxes);
		} catch {
			return null;
		}
	}
	if (!Array.isArray(bboxes)) return null;
	const fallbackPage = Number.isInteger(metadata?.page) ? (metadata.page as number) : 0;
	const rects: HighlightRect[] = [];
	for (const b of bboxes) {
		if (!b || typeof b !== 'object') continue;
		const { x0, y0, x1, y1 } = b as Record<string, unknown>;
		if (![x0, y0, x1, y1].every(isFiniteNumber)) continue;
		if ((x1 as number) <= (x0 as number) || (y1 as number) <= (y0 as number)) continue;
		const page = Number.isInteger((b as Record<string, unknown>).page)
			? ((b as Record<string, unknown>).page as number)
			: fallbackPage;
		rects.push({
			page: page + 1,
			x0: x0 as number,
			y0: y0 as number,
			x1: x1 as number,
			y1: y1 as number
		});
	}
	return rects.length > 0 ? rects : null;
};
