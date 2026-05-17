// Match ONE OR MORE adjacent citation blocks:
// - [1], [1,2], [1#foo], [4#bar] (standard brackets with optional #suffix)
// - 【1】, 【1,2】, 【3†L1-L4】 (Japanese fullwidth brackets with optional †metadata)
const CITATION_RUN_SOURCE =
	'(?:\\[(?:\\d+(?:#[^,\\]\\s]+)?(?:,\\s*\\d+(?:#[^,\\]\\s]+)?)*)\\]|【(?:\\d[\\d,\\s]*)(?:†[^】]*)?】)+';

const INNER_GROUP_SOURCE = '(?:\\[([^\\]]+)\\]|【([\\d,\\s]+)(?:†[^】]*)?】)';

function parseCitationGroups(raw: string): { ids: number[]; citationIdentifiers: string[] } {
	const ids: number[] = [];
	const citationIdentifiers: string[] = [];
	const groupRegex = new RegExp(INNER_GROUP_SOURCE, 'g');
	let m: RegExpExecArray | null;

	while ((m = groupRegex.exec(raw))) {
		if (m[1]) {
			// Standard [] bracket content, e.g. "1, 2#foo"
			const parts = m[1].split(',').map((p) => p.trim());
			parts.forEach((part) => {
				const match = /^(\d+)(?:#(.+))?$/.exec(part);
				if (match) {
					const index = parseInt(match[1], 10);
					if (!isNaN(index)) {
						ids.push(index);
						citationIdentifiers.push(part);
					}
				}
			});
		} else if (m[2]) {
			// Japanese 【】 bracket content, e.g. "1, 2"
			const parsed = m[2]
				.split(',')
				.map((n) => parseInt(n.trim(), 10))
				.filter((n) => !isNaN(n));
			parsed.forEach((index) => {
				ids.push(index);
				citationIdentifiers.push(String(index));
			});
		}
	}
	return { ids, citationIdentifiers };
}

export function citationExtension() {
	return {
		name: 'citation',
		level: 'inline' as const,

		start(src: string) {
			// Trigger on any [number], [number#suffix], 【number】, or 【number†metadata】
			return src.search(/(?:\[\d|【\d)/);
		},

		tokenizer(src: string) {
			// Avoid matching footnotes
			if (/^\[\^/.test(src)) return;

			const rule = new RegExp('^' + CITATION_RUN_SOURCE);
			const match = rule.exec(src);
			if (!match) return;

			const raw = match[0];
			const { ids, citationIdentifiers } = parseCitationGroups(raw);

			if (ids.length === 0) return;

			return {
				type: 'citation',
				raw,
				ids,
				citationIdentifiers
			};
		},

		renderer(token: any) {
			// fallback text
			return token.raw;
		}
	};
}

type WalkableToken = {
	type?: string;
	raw?: string;
	text?: string;
	tokens?: WalkableToken[];
	items?: WalkableToken[];
	header?: WalkableToken[];
	rows?: WalkableToken[][];
	ids?: number[];
	citationIdentifiers?: string[];
};

// [Gradient] Marked invokes custom inline extensions only at the top level of
// inline tokenization. Citation markers nested inside `**`, `_`, `~~`, link
// text, headings, etc. arrive here unsplit (e.g. `**[1]**` becomes
// `strong{ tokens: [text "[1]"] }`). This walker post-processes the token
// tree and splits any `text` token whose content matches the citation pattern
// into a mixed `[text, citation, text]` sequence so the SourceToken renders
// correctly inside emphasis wrappers.
export function applyCitationWalker(tokens: WalkableToken[]): WalkableToken[] {
	if (!Array.isArray(tokens)) return tokens;
	const out: WalkableToken[] = [];
	for (const tok of tokens) {
		const walked = walkToken(tok);
		if (Array.isArray(walked)) out.push(...walked);
		else out.push(walked);
	}
	return out;
}

function walkToken(tok: WalkableToken): WalkableToken | WalkableToken[] {
	if (!tok || typeof tok !== 'object') return tok;

	// Recurse into nested token arrays (paragraphs, headings, emphasis, links,
	// blockquotes, etc.)
	if (Array.isArray(tok.tokens)) {
		tok.tokens = applyCitationWalker(tok.tokens);
	}
	// List items
	if (Array.isArray(tok.items)) {
		for (const item of tok.items) {
			if (item && Array.isArray(item.tokens)) {
				item.tokens = applyCitationWalker(item.tokens);
			}
		}
	}
	// Tables
	if (Array.isArray(tok.header)) {
		for (const cell of tok.header) {
			if (cell && Array.isArray(cell.tokens)) {
				cell.tokens = applyCitationWalker(cell.tokens);
			}
		}
	}
	if (Array.isArray(tok.rows)) {
		for (const row of tok.rows) {
			if (!Array.isArray(row)) continue;
			for (const cell of row) {
				if (cell && Array.isArray(cell.tokens)) {
					cell.tokens = applyCitationWalker(cell.tokens);
				}
			}
		}
	}

	// Split leaf text tokens containing citation patterns
	if (tok.type === 'text' && typeof tok.text === 'string' && !Array.isArray(tok.tokens)) {
		const split = splitTextForCitations(tok.text);
		if (split) return split;
	}

	return tok;
}

function splitTextForCitations(text: string): WalkableToken[] | null {
	const regex = new RegExp(CITATION_RUN_SOURCE, 'g');
	const parts: WalkableToken[] = [];
	let lastIndex = 0;
	let foundAny = false;
	let m: RegExpExecArray | null;

	while ((m = regex.exec(text))) {
		// Skip footnote-style `[^...]` (shouldn't match our regex anyway, but be defensive)
		if (text[m.index + 1] === '^') continue;

		const raw = m[0];
		const { ids, citationIdentifiers } = parseCitationGroups(raw);
		if (ids.length === 0) continue;

		foundAny = true;

		if (m.index > lastIndex) {
			const sub = text.slice(lastIndex, m.index);
			parts.push({ type: 'text', raw: sub, text: sub });
		}
		parts.push({ type: 'citation', raw, ids, citationIdentifiers });
		lastIndex = m.index + raw.length;
	}

	if (!foundAny) return null;

	if (lastIndex < text.length) {
		const sub = text.slice(lastIndex);
		parts.push({ type: 'text', raw: sub, text: sub });
	}

	return parts;
}

export default function () {
	return {
		extensions: [citationExtension()]
	};
}
