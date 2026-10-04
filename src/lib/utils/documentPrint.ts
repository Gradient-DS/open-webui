import { Marked } from 'marked';
import type { DocumentFormat } from './agentDocument';
import { DOCUMENT_SANDBOX, sanitizeDocumentHtml } from './htmlDocument';

const markdown = new Marked({ gfm: true, breaks: false });
const PRINT_STYLE = `
@page { size: A4; margin: 25mm 20mm; }
body { font-family: Arial, sans-serif; font-size: 11pt; line-height: 1.5; color: #1a1a1a; overflow-wrap: anywhere; }
h1 { font-size: 22pt; } h2 { font-size: 16pt; } h3 { font-size: 13pt; }
h1, h2, h3, h4, h5, h6 { break-after: avoid; }
p, li { orphans: 3; widows: 3; }
table { border-collapse: collapse; width: 100%; margin: 4mm 0; font-size: 10pt; }
th, td { border: 1px solid #ccc; padding: 3mm 4mm; text-align: left; }
th { background: #f5f5f5; } thead { display: table-header-group; }
tr, blockquote { break-inside: avoid; }
pre { background: #f6f8fa; padding: 4mm; white-space: pre-wrap; }
pre, code { font-family: 'Courier New', monospace; font-size: 9pt; }
blockquote { border-left: 3px solid #ddd; padding-left: 4mm; color: #555; margin: 3mm 0; }
`;

export function buildPrintDocument(title: string, content: string, format: DocumentFormat): string {
	const html =
		format === 'html'
			? content
			: `<html><head><style>${PRINT_STYLE}</style></head><body>${markdown.parse(content, { async: false })}</body></html>`;
	return sanitizeDocumentHtml(html, title);
}

// [Gradient] Browser printing keeps model-written documents away from server-side fetches.
export function printDocument(
	title: string,
	content: string,
	format: DocumentFormat
): Promise<void> {
	const srcdoc = buildPrintDocument(title, content, format);
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
