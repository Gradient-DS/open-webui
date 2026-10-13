export const SHEET_HEIGHT = 0.72;

export const sourcesSheetClass = (expanded: boolean) =>
	expanded ? 'h-[100dvh] rounded-t-2xl' : 'h-[72dvh] rounded-t-2xl';

/** Where a dragged sheet settles: above halfway to full height it expands, below half the screen it closes. */
export const sheetRelease = (height: number, viewport: number): 'expanded' | 'normal' | 'close' => {
	const share = height / viewport;
	if (share > (SHEET_HEIGHT + 1) / 2) return 'expanded';
	if (share < 0.5) return 'close';
	return 'normal';
};
