// The zoom levels the document panel's − and + step through.
export const ZOOM_STEPS = [0.25, 0.33, 0.5, 0.67, 0.75, 0.9, 1, 1.1, 1.25, 1.5, 1.75, 2, 3];

// The zoom that fits a document into the panel, never enlarged past 100%: a single page (a flyer, a
// certificate) shows whole, more pages fit their width and scroll.
export function fitZoom(
	panel: { width: number; height: number },
	content: { width: number; height: number },
	single: boolean
): number {
	if (panel.width <= 0 || content.width <= 0) return 1;
	const width = panel.width / content.width;
	return Math.min(1, width, single && panel.height > 0 ? panel.height / content.height : width);
}
