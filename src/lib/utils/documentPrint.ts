import { Marked } from 'marked';
import type { DocumentFormat } from './agentDocument';
import { DOCUMENT_SANDBOX, sanitizeDocumentHtml } from './htmlDocument';

// Line breaks as in the document panel, so a source list without list markers stays one line per source.
const markdown = new Marked({ gfm: true, breaks: true });
const PRINT_STYLE = `
@page { size: A4; margin: 22mm 20mm 20mm; }
body { font-family: Inter, Arial, sans-serif; font-size: 10.5pt; line-height: 1.55; color: #1a1a1a; overflow-wrap: break-word; font-feature-settings: 'tnum'; }
h1, h2, h3, h4, h5, h6 { line-height: 1.25; font-weight: 600; break-after: avoid; margin: 1.4em 0 0.5em; }
body > h1:first-child { font-size: 22pt; font-weight: 700; margin: 0 0 8mm; padding-bottom: 4mm; border-bottom: 1px solid #d4d4d4; }
h1 { font-size: 17pt; } h2 { font-size: 14pt; } h3 { font-size: 12pt; } h4, h5, h6 { font-size: 10.5pt; }
p { margin: 0 0 0.7em; } ul, ol { padding-left: 6mm; } li { margin: 0.15em 0; }
p, li { orphans: 3; widows: 3; }
table { border-collapse: collapse; width: 100%; margin: 4mm 0; font-size: 9.5pt; }
th, td { border-bottom: 1px solid #e0e0e0; padding: 2mm 3mm; text-align: left; vertical-align: top; }
th { border-bottom: 1.5px solid #999; font-weight: 600; } tbody tr:nth-child(even) td { background: #f7f7f7; }
thead { display: table-header-group; }
tr, blockquote { break-inside: avoid; }
pre { background: #f6f8fa; padding: 4mm; white-space: pre-wrap; border-radius: 2mm; }
pre, code { font-family: 'Courier New', monospace; font-size: 9pt; }
blockquote { border-left: 3px solid #d4d4d4; padding-left: 4mm; color: #555; margin: 3mm 0; }
hr { border: 0; border-top: 1px solid #d4d4d4; margin: 6mm 0; }
sup { font-size: 0.7em; color: #555; }
`;

// A markdown document opens with its title as a heading, so the first page always carries the title block.
export function withTitle(content: string, title: string): string {
	return /^\s*#\s/.test(content) ? content : `# ${title}\n\n${content}`;
}

let fontFace: Promise<string> | null = null;

// The print frame's CSP only admits data: fonts, so Inter is inlined from our own static assets.
function interFontFace(): Promise<string> {
	fontFace ??= fetch('/assets/fonts/Inter-Variable.ttf')
		.then((res) => (res.ok ? res.blob() : Promise.reject(new Error(`font ${res.status}`))))
		.then(
			(blob) =>
				new Promise<string>((resolve, reject) => {
					const reader = new FileReader();
					reader.onload = () =>
						resolve(
							`@font-face { font-family: Inter; src: url(${reader.result}) format('truetype'); }`
						);
					reader.onerror = () => reject(reader.error);
					reader.readAsDataURL(blob);
				})
		)
		.catch((error) => {
			console.warn('Print font unavailable, falling back to Arial', error);
			fontFace = null;
			return '';
		});
	return fontFace;
}

export function buildPrintDocument(
	title: string,
	content: string,
	format: DocumentFormat,
	font = ''
): string {
	const html =
		format === 'html'
			? content
			: `<html><head><style>${PRINT_STYLE}</style></head><body>${markdown.parse(withTitle(content, title), { async: false })}</body></html>`;
	return sanitizeDocumentHtml(html, title, {
		stripCitations: format === 'html',
		trustedStyle: format === 'html' ? '' : font
	});
}

// [Gradient] Browser printing keeps model-written documents away from server-side fetches.
export async function printDocument(
	title: string,
	content: string,
	format: DocumentFormat
): Promise<void> {
	const font = format === 'html' ? '' : await interFontFace();
	const srcdoc = buildPrintDocument(title, content, format, font);
	return new Promise((resolve, reject) => {
		const frame = document.createElement('iframe');
		frame.title = title;
		frame.setAttribute('aria-hidden', 'true');
		frame.tabIndex = -1;
		// Same-origin permits parent-controlled print(); sandbox and CSP still forbid scripts.
		frame.setAttribute('sandbox', DOCUMENT_SANDBOX);
		frame.style.cssText =
			'position:fixed;left:-10000px;top:0;width:210mm;height:297mm;border:0;opacity:0;pointer-events:none';
		const cleanup = () => {
			clearTimeout(loadTimeout);
			window.removeEventListener('pagehide', cleanup);
			frame.remove();
		};
		const fail = (error: unknown) => {
			cleanup();
			reject(error);
		};
		const loadTimeout = setTimeout(
			() => fail(new Error('Document print frame did not load')),
			15000
		);
		window.addEventListener('pagehide', cleanup, { once: true });
		frame.onerror = () => fail(new Error('Document print frame failed to load'));
		frame.onload = () => {
			clearTimeout(loadTimeout);
			try {
				const printWindow = frame.contentWindow;
				if (!printWindow) throw new Error('Document print window is unavailable');
				printWindow.addEventListener('afterprint', cleanup, { once: true });
				printWindow.print();
				resolve();
			} catch (error) {
				fail(error);
			}
		};
		frame.srcdoc = srcdoc;
		document.body.append(frame);
	});
}
