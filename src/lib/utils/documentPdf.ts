// [Gradient] PDF of an HTML document, built in the browser so model output never reaches a server-side
// renderer. Each page is drawn as an image, exactly as the panel shows it, with an invisible text layer on
// top so the text can be selected, copied and searched.
import type { jsPDF as JsPdf } from 'jspdf';
import type { RawSource } from '$lib/components/chat/Messages/Citations/reduceSources';
import type { DocumentFormat } from './agentDocument';
import { DOCUMENT_SANDBOX, sanitizeDocumentHtml } from './htmlDocument';
import { fitPages, mmToPx, pagesOf, pageSize, pageStyle, type PageSize } from './documentPages';

// Device pixels per CSS pixel in the page images: sharp in print, about 0.5–1.5 MB per page.
const RESOLUTION = 2;
// The characters the PDF's standard font (WinAnsi) can carry; others are left out of the text layer.
const WIN_ANSI_EXTRA = new Set([
	0x20ac, 0x201a, 0x0192, 0x201e, 0x2026, 0x2020, 0x2021, 0x02c6, 0x2030, 0x0160, 0x2039, 0x0152,
	0x017d, 0x2018, 0x2019, 0x201c, 0x201d, 0x2022, 0x2013, 0x2014, 0x02dc, 0x2122, 0x0161, 0x203a,
	0x0153, 0x017e, 0x0178
]);
const winAnsi = (char: string) => {
	const code = char.codePointAt(0)!;
	return (
		(code >= 0x20 && code <= 0x7e) || (code >= 0xa0 && code <= 0xff) || WIN_ANSI_EXTRA.has(code)
	);
};

// One PDF page: the element to draw and, for a document without page sections, which slice of it.
type Area = { element: HTMLElement; top: number };

function openFrame(srcdoc: string, title: string, width: number): Promise<HTMLIFrameElement> {
	return new Promise((resolve, reject) => {
		const frame = document.createElement('iframe');
		frame.title = title;
		frame.setAttribute('aria-hidden', 'true');
		frame.tabIndex = -1;
		// Same-origin lets this page read and draw the frame; sandbox and CSP still forbid scripts.
		frame.setAttribute('sandbox', DOCUMENT_SANDBOX);
		frame.style.cssText = `position:fixed;left:-20000px;top:0;width:${width}px;height:200px;border:0;pointer-events:none`;
		const timeout = setTimeout(() => {
			frame.remove();
			reject(new Error('Document PDF frame did not load'));
		}, 15000);
		frame.onload = () => {
			clearTimeout(timeout);
			resolve(frame);
		};
		frame.srcdoc = srcdoc;
		document.body.append(frame);
	});
}

// Whether an element's text is visible: text hidden on the page (visibility, opacity, a transparent colour) stays
// out of the text layer, so a copied or searched PDF holds nothing the reader could not see.
function shown(element: Element): boolean {
	if (!element.checkVisibility({ opacityProperty: true, visibilityProperty: true })) return false;
	const color = element.ownerDocument.defaultView!.getComputedStyle(element).color;
	return !/^(transparent|rgba\(.*,\s*0\))$/.test(color);
}

// Each word of `area` that falls on its page, written invisibly where it is drawn.
function writeText(pdf: JsPdf, area: Area, size: PageSize): void {
	const doc = area.element.ownerDocument;
	const origin = area.element.getBoundingClientRect();
	const pageHeight = mmToPx(size.height);
	const toMm = size.width / origin.width;
	const walker = doc.createTreeWalker(area.element, NodeFilter.SHOW_TEXT);
	const range = doc.createRange();
	pdf.setFont('helvetica', 'normal');
	while (walker.nextNode()) {
		const node = walker.currentNode as Text;
		if (!node.parentElement || node.parentElement.closest('style, title, script')) continue;
		if (!shown(node.parentElement)) continue;
		for (const word of node.data.matchAll(/\S+/g)) {
			const text = Array.from(word[0]).filter(winAnsi).join('');
			if (!text) continue;
			range.setStart(node, word.index!);
			range.setEnd(node, word.index! + word[0].length);
			const rect = range.getBoundingClientRect();
			const top = rect.top - origin.top - area.top;
			if (!rect.width || !rect.height || top < 0 || top + rect.height / 2 > pageHeight) continue;
			pdf.setFontSize((rect.height * toMm) / 0.3528 / 1.15);
			// Stretched to the drawn word's width, never spaced out: extra spacing reads as gaps between letters.
			pdf.text(text, (rect.left - origin.left) * toMm, (top + rect.height / 2) * toMm, {
				baseline: 'middle',
				renderingMode: 'invisible',
				horizontalScale: (rect.width * toMm) / pdf.getTextWidth(text)
			});
		}
	}
}

export async function buildHtmlPdf(
	title: string,
	content: string,
	page: string | undefined
): Promise<Blob> {
	const size = pageSize(page);
	const [{ jsPDF }, { default: html2canvas }] = await Promise.all([
		import('jspdf'),
		import('html2canvas-pro')
	]);
	const srcdoc = sanitizeDocumentHtml(content, title, {
		stripCitations: true,
		trustedStyle: pageStyle(content, size, 'paper')
	});
	const frame = await openFrame(srcdoc, title, mmToPx(size.width));
	try {
		const doc = frame.contentDocument!;
		await doc.fonts?.ready;
		fitPages(doc, size);
		frame.style.height = `${doc.documentElement.scrollHeight}px`;
		const pageHeight = mmToPx(size.height);
		const sheets = pagesOf(doc);
		const areas: Area[] = sheets.length
			? sheets.map((element) => ({ element, top: 0 }))
			: Array.from(
					{ length: Math.max(1, Math.ceil(doc.documentElement.scrollHeight / pageHeight - 0.02)) },
					(_, index) => ({ element: doc.documentElement, top: index * pageHeight })
				);
		const orientation = size.width > size.height ? 'landscape' : 'portrait';
		const pdf = new jsPDF({
			unit: 'mm',
			format: [size.width, size.height],
			orientation,
			compress: true
		});
		pdf.setProperties({ title });
		for (const [index, area] of areas.entries()) {
			if (index) pdf.addPage([size.width, size.height], orientation);
			// A page section is drawn at its own bounds; a flowing document is cut into page-high slices.
			const canvas = await html2canvas(area.element, {
				scale: RESOLUTION,
				backgroundColor: '#ffffff',
				logging: false,
				...(!sheets.length && { x: 0, y: area.top, width: mmToPx(size.width), height: pageHeight })
			});
			const image = canvas.toDataURL('image/jpeg', 0.92);
			pdf.addImage(image, 'JPEG', 0, 0, size.width, size.height, undefined, 'FAST');
			writeText(pdf, area, size);
		}
		return pdf.output('blob');
	} finally {
		frame.remove();
	}
}

// The PDF download of a writer document: an HTML document as its pages, a markdown document as text.
export async function documentPdf(
	document: { title: string; content: string; format: DocumentFormat; page?: string },
	sources: RawSource[],
	sourcesHeading: string
): Promise<Blob> {
	if (document.format === 'html')
		return buildHtmlPdf(document.title, document.content, document.page);
	const { buildDocumentPdf } = await import('./documentPdfMarkdown');
	return buildDocumentPdf(document.title, document.content, sources, sourcesHeading);
}
