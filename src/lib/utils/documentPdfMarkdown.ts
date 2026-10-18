// [Gradient] PDF of a markdown document, built in the browser with pdfmake so model output never reaches a
// server-side renderer. Real text (selectable, searchable), A4 pages with numbers, and like the Word export
// citations stay [n] markers with one source list at the end; only embedded png/jpeg images are kept.
import type { Content, TDocumentDefinitions } from 'pdfmake/interfaces';
import { Marked, type Token, type Tokens } from 'marked';
import type { RawSource } from '$lib/components/chat/Messages/Citations/reduceSources';
import { buildFullSourceList, normalizeCitations } from './citations';
import { withTitle } from './documentPrint';

const markdown = new Marked({ gfm: true, breaks: true });
// A4 in points, less the page margins.
const TEXT_WIDTH = 595.28 - 2 * 57;

type Style = { bold?: boolean; italics?: boolean; strike?: boolean; code?: boolean };
type Run = Exclude<Content, string> & { text: string | Run[] };

const unescape = (text: string) =>
	text
		.replace(/&lt;/g, '<')
		.replace(/&gt;/g, '>')
		.replace(/&quot;/g, '"')
		.replace(/&#39;/g, "'")
		.replace(/&amp;/g, '&');

const safeLink = (href: string) => /^(https?:|mailto:)/i.test(href);
const embeddedImage = (href: string) => /^data:image\/(png|jpe?g);base64,/i.test(href);

// Roboto has no emoji: left in, they print as empty boxes.
const EMOJI =
	/(?:\p{Emoji_Presentation}|\p{Extended_Pictographic}\u{FE0F})(?:\u{200D}\p{Extended_Pictographic}|[\u{FE0F}\p{Emoji_Modifier}])*\s?/gu;

function run(text: string, style: Style): Run {
	return {
		text: text.replace(EMOJI, ''),
		...(style.bold && { bold: true }),
		...(style.italics && { italics: true }),
		...(style.strike && { decoration: 'lineThrough' }),
		...(style.code && { font: 'Courier', fontSize: 9 })
	} as Run;
}

function inline(tokens: Token[] = [], style: Style = {}): Run[] {
	return tokens.flatMap((token): Run[] => {
		switch (token.type) {
			case 'strong':
				return inline(token.tokens, { ...style, bold: true });
			case 'em':
				return inline(token.tokens, { ...style, italics: true });
			case 'del':
				return inline(token.tokens, { ...style, strike: true });
			case 'codespan':
				return [run(unescape(token.text), { ...style, code: true })];
			case 'br':
				return [run('\n', style)];
			case 'link':
				return safeLink(token.href)
					? [
							{
								text: inline(token.tokens, style),
								link: token.href,
								color: '#1d4ed8',
								decoration: 'underline'
							} as Run
						]
					: inline(token.tokens, style);
			case 'image':
				return token.text ? [run(unescape(token.text), { ...style, italics: true })] : [];
			case 'html':
				return /^<br\s*\/?>$/i.test(token.text.trim()) ? [run('\n', style)] : [];
			case 'text':
			case 'escape':
				return 'tokens' in token && token.tokens?.length
					? inline(token.tokens, style)
					: [run(unescape(token.text), style)];
			default:
				return 'text' in token && typeof token.text === 'string'
					? [run(unescape(token.text), style)]
					: [];
		}
	});
}

// A block in a box: a quote's bar on the left, or a code block's shaded background.
function boxed(content: Content, bar: boolean): Content {
	return {
		table: { widths: ['*'], body: [[content]] },
		layout: {
			hLineWidth: () => 0,
			vLineWidth: (i: number) => (bar && i === 0 ? 2 : 0),
			vLineColor: () => '#d4d4d4',
			fillColor: () => (bar ? null : '#f6f8fa'),
			paddingLeft: () => 8,
			paddingRight: () => 8,
			paddingTop: () => (bar ? 0 : 6),
			paddingBottom: () => (bar ? 0 : 6)
		},
		margin: [0, 2, 0, 8]
	};
}

function block(token: Token): Content[] {
	switch (token.type) {
		case 'heading':
			return [{ text: inline(token.tokens), style: `h${Math.min(token.depth, 4)}` }];
		case 'paragraph': {
			const only = token.tokens?.length === 1 ? token.tokens[0] : null;
			if (only?.type === 'image' && embeddedImage(only.href))
				return [{ image: only.href, fit: [TEXT_WIDTH, 600], margin: [0, 4, 0, 8] }];
			return [{ text: inline(token.tokens), style: 'paragraph' }];
		}
		case 'text':
			return [
				{
					text: inline(
						'tokens' in token && token.tokens
							? token.tokens
							: [{ type: 'text', raw: token.text, text: token.text } as Token]
					),
					style: 'paragraph'
				}
			];
		case 'blockquote':
			return [
				boxed({ stack: (token as Tokens.Blockquote).tokens.flatMap(block), color: '#555555' }, true)
			];
		case 'list':
			return [list(token as Tokens.List)];
		case 'code':
			return [
				boxed(
					{ text: token.text, font: 'Courier', fontSize: 8.5, preserveLeadingSpaces: true },
					false
				)
			];
		case 'table':
			return [table(token as Tokens.Table)];
		case 'hr':
			return [
				{
					canvas: [
						{
							type: 'line',
							x1: 0,
							y1: 0,
							x2: TEXT_WIDTH,
							y2: 0,
							lineWidth: 0.5,
							lineColor: '#d4d4d4'
						}
					],
					margin: [0, 8, 0, 12]
				}
			];
		case 'html': {
			const text = token.text.replace(/<[^>]*>/g, '').trim();
			return text ? [{ text: unescape(text), style: 'paragraph' }] : [];
		}
		default:
			return [];
	}
}

function list(token: Tokens.List): Content {
	const items = token.items.map((item): Content => {
		const children = item.tokens.flatMap((child) =>
			child.type === 'text'
				? [{ text: inline('tokens' in child ? child.tokens : []) }]
				: block(child)
		);
		return children.length === 1 ? children[0] : { stack: children };
	});
	return token.ordered
		? { ol: items, start: typeof token.start === 'number' ? token.start : 1, margin: [0, 0, 0, 8] }
		: { ul: items, margin: [0, 0, 0, 8] };
}

function table(token: Tokens.Table): Content {
	return {
		table: {
			headerRows: 1,
			widths: token.header.map(() => '*'),
			body: [
				token.header.map((cell) => ({
					text: inline(cell.tokens, { bold: true }),
					fillColor: '#f2f2f2'
				})),
				...token.rows.map((row) => row.map((cell) => ({ text: inline(cell.tokens) })))
			]
		},
		layout: 'lightHorizontalLines',
		fontSize: 9.5,
		margin: [0, 4, 0, 10]
	};
}

export async function buildDocumentPdf(
	title: string,
	content: string,
	rawSources: RawSource[],
	sourcesHeading: string
): Promise<Blob> {
	const [{ default: pdfMake }, { default: vfs }, { default: courier }] = await Promise.all([
		import('pdfmake/build/pdfmake'),
		import('pdfmake/build/vfs_fonts'),
		import('pdfmake/build/standard-fonts/Courier')
	]);
	pdfMake.addVirtualFileSystem(vfs);
	pdfMake.addFontContainer(courier);

	const normalized = normalizeCitations(content, rawSources);
	// Trimmed: agent documents open with a newline, and a leading blank token would become an empty title.
	const [first, ...rest] = markdown.lexer(withTitle(normalized.content.trimStart(), title));
	const body: Content[] = [
		{ text: inline((first as Tokens.Heading).tokens), style: 'title' },
		...rest.flatMap(block)
	];
	// One entry per source, numbered as in the text; a body without markers lists every source.
	const listed = normalized.sourceList.length
		? normalized.sourceList
		: buildFullSourceList(rawSources);
	if (listed.length) {
		body.push({ text: sourcesHeading, style: 'h2' });
		for (const source of listed) {
			const url = source.url && source.url !== source.name ? ` — ${source.url}` : '';
			body.push({ text: `[${source.index}] ${source.name}${url}`, style: 'paragraph' });
		}
	}

	const definition: TDocumentDefinitions = {
		info: { title },
		pageSize: 'A4',
		pageMargins: [57, 64, 57, 64],
		content: body,
		defaultStyle: { font: 'Roboto', fontSize: 10.5, lineHeight: 1.3, color: '#1a1a1a' },
		styles: {
			title: { fontSize: 22, bold: true, margin: [0, 0, 0, 16] },
			h1: { fontSize: 17, bold: true, margin: [0, 16, 0, 6] },
			h2: { fontSize: 14, bold: true, margin: [0, 14, 0, 5] },
			h3: { fontSize: 12, bold: true, margin: [0, 12, 0, 4] },
			h4: { fontSize: 10.5, bold: true, margin: [0, 10, 0, 4] },
			paragraph: { margin: [0, 0, 0, 8] }
		},
		footer: (current: number, total: number) => ({
			text: `${current} / ${total}`,
			alignment: 'right',
			fontSize: 9,
			color: '#888888',
			margin: [0, 24, 57, 0]
		})
	};
	return pdfMake.createPdf(definition).getBlob();
}
