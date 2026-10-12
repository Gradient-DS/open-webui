// [Gradient] Word export of a writer document, built in the browser so model output never reaches a
// server-side renderer. Citations become Word footnotes; only embedded (data:) images are kept.
import {
	AlignmentType,
	BorderStyle,
	Document,
	ExternalHyperlink,
	Footer,
	FootnoteReferenceRun,
	HeadingLevel,
	ImageRun,
	LevelFormat,
	Packer,
	PageNumber,
	Paragraph,
	ShadingType,
	Table,
	TableCell,
	TableRow,
	TextRun,
	WidthType,
	type ParagraphChild
} from 'docx';
import { Marked, type Token, type Tokens } from 'marked';
import type { RawSource } from '$lib/components/chat/Messages/Citations/reduceSources';
import { buildFullSourceList, normalizeCitations, type SourceInfo } from './citations';
import { withTitle } from './documentPrint';

const markdown = new Marked({ gfm: true, breaks: true });
const CITATION = /\[(\d+(?:,\s*\d+)*)\]/g;
const HEADINGS = [
	HeadingLevel.HEADING_1,
	HeadingLevel.HEADING_2,
	HeadingLevel.HEADING_3,
	HeadingLevel.HEADING_4,
	HeadingLevel.HEADING_5,
	HeadingLevel.HEADING_6
];
const IMAGE_TYPES = { 'image/png': 'png', 'image/jpeg': 'jpg', 'image/gif': 'gif' } as const;
const MAX_IMAGE_WIDTH = 600;
const MONO = 'Courier New';

type Style = { bold?: boolean; italics?: boolean; strike?: boolean; code?: boolean };
type Image = {
	data: Uint8Array;
	type: (typeof IMAGE_TYPES)[keyof typeof IMAGE_TYPES];
	width: number;
	height: number;
};

const unescape = (text: string) =>
	text
		.replace(/&lt;/g, '<')
		.replace(/&gt;/g, '>')
		.replace(/&quot;/g, '"')
		.replace(/&#39;/g, "'")
		.replace(/&amp;/g, '&');

const safeLink = (href: string) => /^(https?:|mailto:)/i.test(href);

async function loadImage(href: string): Promise<Image | null> {
	const match = /^data:(image\/(?:png|jpeg|gif));base64,([A-Za-z0-9+/=\s]+)$/i.exec(href);
	if (!match) return null;
	const element = new Image();
	element.src = href;
	try {
		await element.decode();
	} catch {
		return null;
	}
	const scale = Math.min(1, MAX_IMAGE_WIDTH / (element.naturalWidth || 1));
	return {
		data: Uint8Array.from(atob(match[2].replace(/\s/g, '')), (c) => c.charCodeAt(0)),
		type: IMAGE_TYPES[match[1].toLowerCase() as keyof typeof IMAGE_TYPES],
		width: Math.round(element.naturalWidth * scale),
		height: Math.round(element.naturalHeight * scale)
	};
}

class DocxWriter {
	footnotes: Record<number, { children: Paragraph[] }> = {};
	private nextFootnote = 1;
	private nextList = 0;

	constructor(
		private sources: SourceInfo[],
		private images: Map<string, Image>
	) {}

	private run(text: string, style: Style): TextRun {
		return new TextRun({
			text,
			bold: style.bold,
			italics: style.italics,
			strike: style.strike,
			...(style.code && { font: MONO, size: 20 })
		});
	}

	private citedText(text: string, style: Style): ParagraphChild[] {
		const children: ParagraphChild[] = [];
		let last = 0;
		for (const match of text.matchAll(CITATION)) {
			const numbers = match[1].split(',').map((n) => Number(n.trim()));
			const cited = numbers.map((n) => this.sources.find((source) => source.index === n));
			if (cited.some((source) => !source)) continue;
			const before = text.slice(last, match.index).replace(/ $/, '');
			if (before) children.push(this.run(before, style));
			for (const source of cited as SourceInfo[]) {
				const id = this.nextFootnote++;
				const label =
					source.url && source.url !== source.name ? `${source.name} — ${source.url}` : source.name;
				this.footnotes[id] = { children: [new Paragraph({ children: [new TextRun(label)] })] };
				children.push(new FootnoteReferenceRun(id));
			}
			last = match.index! + match[0].length;
		}
		const rest = text.slice(last);
		if (rest) children.push(this.run(rest, style));
		return children;
	}

	inline(tokens: Token[] = [], style: Style = {}): ParagraphChild[] {
		return tokens.flatMap((token): ParagraphChild[] => {
			switch (token.type) {
				case 'strong':
					return this.inline(token.tokens, { ...style, bold: true });
				case 'em':
					return this.inline(token.tokens, { ...style, italics: true });
				case 'del':
					return this.inline(token.tokens, { ...style, strike: true });
				case 'codespan':
					return [this.run(unescape(token.text), { ...style, code: true })];
				case 'br':
					return [new TextRun({ break: 1 })];
				case 'link': {
					if (!safeLink(token.href)) return this.inline(token.tokens, style);
					const text = new TextRun({ text: unescape(token.text), style: 'Hyperlink' });
					return [new ExternalHyperlink({ link: token.href, children: [text] })];
				}
				case 'image': {
					const image = this.images.get(token.href);
					return image
						? [
								new ImageRun({
									type: image.type,
									data: image.data,
									transformation: { width: image.width, height: image.height }
								})
							]
						: token.text
							? [this.run(unescape(token.text), { ...style, italics: true })]
							: [];
				}
				case 'html':
					return /^<br\s*\/?>$/i.test(token.text.trim()) ? [new TextRun({ break: 1 })] : [];
				case 'text':
				case 'escape':
					return 'tokens' in token && token.tokens?.length
						? this.inline(token.tokens, style)
						: this.citedText(unescape(token.text), style);
				default:
					return 'text' in token && typeof token.text === 'string'
						? [this.run(unescape(token.text), style)]
						: [];
			}
		});
	}

	block(token: Token, quote = 0): (Paragraph | Table)[] {
		const indent = quote
			? {
					indent: { left: 360 * quote },
					border: { left: { style: BorderStyle.SINGLE, size: 12, color: 'CCCCCC', space: 8 } }
				}
			: {};
		switch (token.type) {
			case 'heading':
				return [
					new Paragraph({ heading: HEADINGS[token.depth - 1], children: this.inline(token.tokens) })
				];
			case 'paragraph':
			case 'text':
				return [
					new Paragraph({
						...indent,
						children: this.inline(
							'tokens' in token && token.tokens
								? token.tokens
								: [{ type: 'text', raw: token.text, text: token.text } as Token]
						)
					})
				];
			case 'blockquote':
				return (token as Tokens.Blockquote).tokens.flatMap((child) => this.block(child, quote + 1));
			case 'list':
				return this.list(token as Tokens.List, 0);
			case 'code':
				return [
					new Paragraph({
						shading: { type: ShadingType.CLEAR, fill: 'F6F8FA', color: 'auto' },
						children: token.text
							.split('\n')
							.flatMap((line: string, i: number) => [
								...(i ? [new TextRun({ break: 1 })] : []),
								new TextRun({ text: line, font: MONO, size: 18 })
							])
					})
				];
			case 'table':
				return [this.table(token as Tokens.Table)];
			case 'hr':
				return [
					new Paragraph({
						border: { bottom: { style: BorderStyle.SINGLE, size: 6, color: 'CCCCCC', space: 1 } },
						children: []
					})
				];
			case 'html': {
				const text = token.text.replace(/<[^>]*>/g, '').trim();
				return text ? [new Paragraph({ ...indent, children: [new TextRun(unescape(text))] })] : [];
			}
			default:
				return [];
		}
	}

	private list(list: Tokens.List, level: number): (Paragraph | Table)[] {
		const instance = this.nextList++;
		return list.items.flatMap((item) =>
			item.tokens.flatMap((child, i) => {
				if (child.type === 'list') return this.list(child as Tokens.List, level + 1);
				const children =
					child.type === 'text' || child.type === 'paragraph'
						? this.inline('tokens' in child ? child.tokens : [])
						: [];
				if (i > 0 || !children.length) return this.block(child);
				return [
					new Paragraph({
						children,
						...(list.ordered
							? { numbering: { reference: 'ordered', level, instance } }
							: { bullet: { level } })
					})
				];
			})
		);
	}

	private table(table: Tokens.Table): Table {
		const cell = (tokens: Token[], header: boolean) =>
			new TableCell({
				children: [new Paragraph({ children: this.inline(tokens, { bold: header }) })],
				...(header && { shading: { type: ShadingType.CLEAR, fill: 'F2F2F2', color: 'auto' } }),
				margins: { top: 60, bottom: 60, left: 100, right: 100 }
			});
		return new Table({
			width: { size: 100, type: WidthType.PERCENTAGE },
			rows: [
				new TableRow({
					tableHeader: true,
					children: table.header.map((c) => cell(c.tokens, true))
				}),
				...table.rows.map(
					(row) => new TableRow({ children: row.map((c) => cell(c.tokens, false)) })
				)
			]
		});
	}
}

export async function buildDocumentDocx(
	title: string,
	content: string,
	rawSources: RawSource[],
	sourcesHeading: string
): Promise<Blob> {
	const normalized = normalizeCitations(content, rawSources);
	const tokens = markdown.lexer(withTitle(normalized.content, title));
	const hrefs = new Set<string>();
	markdown.walkTokens(tokens, (token) => {
		if (token.type === 'image') hrefs.add(token.href);
	});
	const images = new Map<string, Image>();
	for (const href of hrefs) {
		const image = await loadImage(href);
		if (image) images.set(href, image);
	}

	const writer = new DocxWriter(normalized.sourceList, images);
	const [first, ...rest] = tokens;
	const children: (Paragraph | Table)[] = [
		new Paragraph({
			heading: HeadingLevel.TITLE,
			children: writer.inline((first as Tokens.Heading).tokens)
		}),
		...rest.flatMap((token) => writer.block(token))
	];
	// Sources the body never cites inline still get listed, as in the other downloads.
	const uncited = normalized.sourceList.length ? [] : buildFullSourceList(rawSources);
	if (uncited.length) {
		children.push(
			new Paragraph({ heading: HeadingLevel.HEADING_2, children: [new TextRun(sourcesHeading)] })
		);
		for (const source of uncited) {
			const url = source.url && source.url !== source.name ? ` — ${source.url}` : '';
			children.push(
				new Paragraph({ children: [new TextRun(`[${source.index}] ${source.name}${url}`)] })
			);
		}
	}

	const doc = new Document({
		title,
		styles: {
			default: {
				document: {
					run: { font: 'Calibri', size: 22, color: '1A1A1A' },
					paragraph: { spacing: { after: 120, line: 276 } }
				}
			},
			paragraphStyles: [
				{
					id: 'Title',
					name: 'Title',
					basedOn: 'Normal',
					next: 'Normal',
					run: { size: 40, bold: true },
					paragraph: {
						spacing: { after: 240 },
						border: { bottom: { style: BorderStyle.SINGLE, size: 6, color: 'CCCCCC', space: 6 } }
					}
				},
				{
					id: 'Heading1',
					name: 'Heading 1',
					basedOn: 'Normal',
					next: 'Normal',
					run: { size: 32, bold: true },
					paragraph: { spacing: { before: 360, after: 120 }, keepNext: true }
				},
				{
					id: 'Heading2',
					name: 'Heading 2',
					basedOn: 'Normal',
					next: 'Normal',
					run: { size: 28, bold: true },
					paragraph: { spacing: { before: 280, after: 100 }, keepNext: true }
				},
				{
					id: 'Heading3',
					name: 'Heading 3',
					basedOn: 'Normal',
					next: 'Normal',
					run: { size: 24, bold: true },
					paragraph: { spacing: { before: 240, after: 80 }, keepNext: true }
				}
			]
		},
		numbering: {
			config: [
				{
					reference: 'ordered',
					levels: Array.from({ length: 9 }, (_, level) => ({
						level,
						format: LevelFormat.DECIMAL,
						text: `%${level + 1}.`,
						alignment: AlignmentType.START,
						style: { paragraph: { indent: { left: 720 * (level + 1), hanging: 360 } } }
					}))
				}
			]
		},
		footnotes: writer.footnotes,
		sections: [
			{
				children,
				properties: {
					page: {
						size: { width: 11906, height: 16838 },
						margin: { top: 1418, bottom: 1418, left: 1134, right: 1134 }
					}
				},
				footers: {
					default: new Footer({
						children: [
							new Paragraph({
								alignment: AlignmentType.RIGHT,
								children: [
									new TextRun({
										children: [PageNumber.CURRENT, ' / ', PageNumber.TOTAL_PAGES],
										size: 18,
										color: '888888'
									})
								]
							})
						]
					})
				}
			}
		]
	});
	return Packer.toBlob(doc);
}
