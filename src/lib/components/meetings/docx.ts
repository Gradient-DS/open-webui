// [Gradient] Vergadering: Markdown to a minimal .docx in the browser (jszip only, no server round-trip).
import { marked, type Token, type Tokens } from 'marked';

export type Run = { text: string; bold?: boolean; italic?: boolean };
export type Block =
	| { kind: 'heading'; level: number; runs: Run[] }
	| { kind: 'paragraph'; runs: Run[] }
	| { kind: 'item'; marker: string; depth: number; runs: Run[] };

const inlineRuns = (tokens: Token[] | undefined, style: Omit<Run, 'text'> = {}): Run[] => {
	const runs: Run[] = [];
	for (const token of tokens ?? []) {
		if (token.type === 'strong') runs.push(...inlineRuns(token.tokens, { ...style, bold: true }));
		else if (token.type === 'em')
			runs.push(...inlineRuns(token.tokens, { ...style, italic: true }));
		else if (token.type === 'br') runs.push({ ...style, text: '\n' });
		else if ('tokens' in token && token.tokens?.length)
			runs.push(...inlineRuns(token.tokens, style));
		else if ('text' in token) runs.push({ ...style, text: decode(String(token.text)) });
	}
	return runs;
};

const decode = (text: string): string =>
	text
		.replace(/&quot;/g, '"')
		.replace(/&#39;/g, "'")
		.replace(/&lt;/g, '<')
		.replace(/&gt;/g, '>')
		.replace(/&amp;/g, '&');

const listBlocks = (list: Tokens.List, depth: number): Block[] => {
	const blocks: Block[] = [];
	list.items.forEach((item, index) => {
		const marker = list.ordered ? `${Number(list.start || 1) + index}.` : '•';
		const runs: Run[] = [];
		const nested: Block[] = [];
		for (const child of item.tokens) {
			if (child.type === 'list') nested.push(...listBlocks(child as Tokens.List, depth + 1));
			else if ('tokens' in child && child.tokens) runs.push(...inlineRuns(child.tokens));
			else if ('text' in child) runs.push({ text: decode(String(child.text)) });
		}
		blocks.push({ kind: 'item', marker, depth, runs }, ...nested);
	});
	return blocks;
};

export const markdownBlocks = (markdown: string): Block[] => {
	const blocks: Block[] = [];
	for (const token of marked.lexer(markdown)) {
		if (token.type === 'heading') {
			blocks.push({ kind: 'heading', level: token.depth, runs: inlineRuns(token.tokens) });
		} else if (token.type === 'paragraph' || token.type === 'text') {
			blocks.push({
				kind: 'paragraph',
				runs: inlineRuns((token as Tokens.Paragraph).tokens ?? [token])
			});
		} else if (token.type === 'list') {
			blocks.push(...listBlocks(token as Tokens.List, 0));
		} else if (token.type === 'blockquote') {
			for (const block of markdownBlocks(token.text)) blocks.push(block);
		} else if (token.type === 'code') {
			blocks.push({ kind: 'paragraph', runs: [{ text: token.text }] });
		} else if (token.type === 'table') {
			const table = token as Tokens.Table;
			const row = (cells: Tokens.TableCell[]) => cells.map((cell) => decode(cell.text)).join(' | ');
			blocks.push({ kind: 'paragraph', runs: [{ text: row(table.header), bold: true }] });
			for (const cells of table.rows)
				blocks.push({ kind: 'paragraph', runs: [{ text: row(cells) }] });
		}
	}
	return blocks;
};

// XML 1.0 forbids most control characters; Word refuses a file that has them.
// eslint-disable-next-line no-control-regex
const INVALID_XML = /[\u0000-\u0008\u000B\u000C\u000E-\u001F￾￿]/g;

export const escapeXml = (text: string): string =>
	text
		.replace(INVALID_XML, '')
		.replace(/&/g, '&amp;')
		.replace(/</g, '&lt;')
		.replace(/>/g, '&gt;')
		.replace(/"/g, '&quot;');

const runXml = (run: Run): string => {
	const props = `${run.bold ? '<w:b/>' : ''}${run.italic ? '<w:i/>' : ''}`;
	const rPr = props ? `<w:rPr>${props}</w:rPr>` : '';
	return run.text
		.split('\n')
		.map(
			(part, index) =>
				`${index > 0 ? '<w:r><w:br/></w:r>' : ''}<w:r>${rPr}<w:t xml:space="preserve">${escapeXml(part)}</w:t></w:r>`
		)
		.join('');
};

const blockXml = (block: Block): string => {
	if (block.kind === 'heading') {
		const style = `Heading${Math.min(block.level, 3)}`;
		return `<w:p><w:pPr><w:pStyle w:val="${style}"/></w:pPr>${block.runs.map(runXml).join('')}</w:p>`;
	}
	if (block.kind === 'item') {
		const indent = 360 * (block.depth + 1);
		return `<w:p><w:pPr><w:ind w:left="${indent + 360}" w:hanging="360"/></w:pPr>${runXml({ text: `${block.marker}\t` })}${block.runs.map(runXml).join('')}</w:p>`;
	}
	return `<w:p>${block.runs.map(runXml).join('')}</w:p>`;
};

const W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main';

export const documentXml = (blocks: Block[]): string =>
	`<?xml version="1.0" encoding="UTF-8" standalone="yes"?>` +
	`<w:document xmlns:w="${W}"><w:body>${blocks.map(blockXml).join('')}` +
	`<w:sectPr><w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:top="1418" w:right="1134" w:bottom="1418" w:left="1134" w:header="709" w:footer="709" w:gutter="0"/></w:sectPr>` +
	`</w:body></w:document>`;

const heading = (level: number, size: number) =>
	`<w:style w:type="paragraph" w:styleId="Heading${level}"><w:name w:val="heading ${level}"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:pPr><w:keepNext/><w:spacing w:before="240" w:after="120"/><w:outlineLvl w:val="${level - 1}"/></w:pPr><w:rPr><w:b/><w:sz w:val="${size}"/></w:rPr></w:style>`;

const STYLES_XML =
	`<?xml version="1.0" encoding="UTF-8" standalone="yes"?>` +
	`<w:styles xmlns:w="${W}">` +
	`<w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri" w:cs="Calibri"/><w:sz w:val="22"/><w:lang w:val="nl-NL"/></w:rPr></w:rPrDefault><w:pPrDefault><w:pPr><w:spacing w:after="120" w:line="276" w:lineRule="auto"/></w:pPr></w:pPrDefault></w:docDefaults>` +
	`<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/></w:style>` +
	heading(1, 36) +
	heading(2, 28) +
	heading(3, 24) +
	`</w:styles>`;

const CONTENT_TYPES =
	`<?xml version="1.0" encoding="UTF-8" standalone="yes"?>` +
	`<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">` +
	`<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>` +
	`<Default Extension="xml" ContentType="application/xml"/>` +
	`<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>` +
	`<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>` +
	`</Types>`;

const ROOT_RELS =
	`<?xml version="1.0" encoding="UTF-8" standalone="yes"?>` +
	`<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">` +
	`<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>` +
	`</Relationships>`;

const DOCUMENT_RELS =
	`<?xml version="1.0" encoding="UTF-8" standalone="yes"?>` +
	`<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">` +
	`<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>` +
	`</Relationships>`;

export const DOCX_MIME = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document';

export const markdownToDocx = async (markdown: string): Promise<Blob> => {
	const { default: JSZip } = await import('jszip');
	const zip = new JSZip();
	zip.file('[Content_Types].xml', CONTENT_TYPES);
	zip.file('_rels/.rels', ROOT_RELS);
	zip.file('word/document.xml', documentXml(markdownBlocks(markdown)));
	zip.file('word/styles.xml', STYLES_XML);
	zip.file('word/_rels/document.xml.rels', DOCUMENT_RELS);
	return zip.generateAsync({ type: 'blob', mimeType: DOCX_MIME });
};
