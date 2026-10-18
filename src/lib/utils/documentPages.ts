// [Gradient] Pages of an HTML document: the model writes each page as a <section class="page"> and names its
// size on the document tag (`page="a4-landscape"`); we set the size, so the preview, the PDF and print agree.

export const PAGE_SIZES = {
	a4: [210, 297],
	'a4-landscape': [297, 210],
	a5: [148, 210],
	'a5-landscape': [210, 148],
	letter: [215.9, 279.4],
	'letter-landscape': [279.4, 215.9]
} as const;

export type PageName = keyof typeof PAGE_SIZES;
export type PageSize = { name: PageName; width: number; height: number };

const PX_PER_MM = 96 / 25.4;
export const mmToPx = (mm: number) => mm * PX_PER_MM;

export function pageSize(name: string | undefined): PageSize {
	const key = (name ?? '').toLowerCase() as PageName;
	const [width, height] = PAGE_SIZES[key] ?? PAGE_SIZES.a4;
	return { name: key in PAGE_SIZES ? key : 'a4', width, height };
}

// Space around the sheets in the preview, inside the frame, so their shadows are not cut off.
export const SHEET_GAP = 28;

// Whether an HTML document is written as page sections; one written as a single flowing page keeps the
// layout it had before pages existed.
export function hasPages(content: string): boolean {
	return /<section\b[^>]*\bclass\s*=\s*["'](?:[^"']*\s)?page(?:\s[^"']*)?["']/i.test(content);
}

// The page rules, after the model's own styles, for a document with page sections (none otherwise). `screen`
// lays the pages out as sheets with a gap and a shadow; `paper` puts them edge to edge for the PDF and print.
// The model styles a page's look, never its size.
export function pageStyle(content: string, size: PageSize, mode: 'screen' | 'paper'): string {
	if (!hasPages(content)) return '';
	const sheet = `width: ${size.width}mm !important; height: ${size.height}mm !important; min-height: 0 !important; max-width: none !important; max-height: none !important; overflow: hidden !important; box-sizing: border-box !important; position: relative;`;
	const common = `@page { size: ${size.width}mm ${size.height}mm; margin: 0; }
html, body { margin: 0 !important; padding: 0 !important; width: auto !important; min-height: 0 !important; -webkit-print-color-adjust: exact; print-color-adjust: exact; }
:where(section.page) { background: #fff; }
section.page { ${sheet} margin: 0 !important; break-after: page; break-inside: avoid; }
section.page:last-of-type { break-after: auto; }`;
	if (mode === 'paper') return common;
	return `${common}
html, body { background: transparent !important; }
body { padding: ${SHEET_GAP}px !important; }
section.page { margin: 0 auto ${SHEET_GAP}px !important; box-shadow: 0 6px 28px rgba(0, 0, 0, 0.35); }
section.page:last-of-type { margin-bottom: 0 !important; }`;
}

export function pagesOf(doc: Document): HTMLElement[] {
	return Array.from(doc.querySelectorAll<HTMLElement>('section.page'));
}

// A page holding more than fits is laid out larger and zoomed down to its size, so nothing is cut off and its
// own layout (grids, columns) stays as written.
export function fitPages(doc: Document, size: PageSize): void {
	const width = mmToPx(size.width);
	const height = mmToPx(size.height);
	for (const page of pagesOf(doc)) {
		page.style.removeProperty('zoom');
		page.style.removeProperty('width');
		page.style.removeProperty('height');
		const over = Math.max(page.scrollHeight / height, page.scrollWidth / width);
		if (over <= 1.01) continue;
		page.style.setProperty('zoom', String(1 / over));
		page.style.setProperty('width', `${width * over}px`, 'important');
		page.style.setProperty('height', `${height * over}px`, 'important');
	}
}
