// The zoom levels the document panel's − and + step through.
export const ZOOM_STEPS = [0.25, 0.33, 0.5, 0.67, 0.75, 0.9, 1, 1.1, 1.25, 1.5, 1.75, 2, 3];

// A4 at 96 dpi.
export const PAGE_WIDTH = (210 / 25.4) * 96;
export const PAGE_HEIGHT = (297 / 25.4) * 96;

// The zoom that fits a page into the panel, never enlarged past 100%: a single page (a flyer, a certificate)
// shows whole, a longer document fits its width and scrolls.
export function fitZoom(
	panel: { width: number; height: number },
	page: { width: number; height: number }
): number {
	if (panel.width <= 0 || page.width <= 0) return 1;
	const width = panel.width / page.width;
	const single = page.height <= PAGE_HEIGHT * 1.05 && panel.height > 0;
	return Math.min(1, width, single ? panel.height / page.height : width);
}
