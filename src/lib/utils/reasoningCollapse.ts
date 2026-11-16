// [Gradient] Opened reasoning collapses on a click in its text, except while the reader selects text or uses a
// control or code block inside it.
const KEEPS_OPEN = 'a, button, input, textarea, select, summary, label, [role="button"], pre';

export function collapsesOnClick(target: EventTarget | null, selectedText: string): boolean {
	if (selectedText.length > 0) return false;
	return !(target instanceof Element && target.closest(KEEPS_OPEN));
}
