import { describe, expect, it } from 'vitest';
import { Marked } from 'marked';
import citationExtension, { applyCitationWalker } from './citation-extension';

const marked = new Marked(citationExtension());

type Token = { type?: string; raw?: string; ids?: number[]; tokens?: Token[]; items?: Token[] };

// The token tree as a compact string: `strong(C1 " text")`.
const shape = (tokens: Token[]): string =>
	tokens
		.map((t) =>
			t.type === 'citation'
				? `C${t.ids?.join(',')}`
				: t.items
					? `${t.type}(${shape(t.items)})`
					: t.tokens
						? `${t.type}(${shape(t.tokens)})`
						: t.type === 'text'
							? JSON.stringify(t.raw)
							: t.type
		)
		.join(' ');

const render = (markdown: string) => shape(applyCitationWalker(marked.lexer(markdown)));

describe('citations inside emphasis', () => {
	it.each([
		['- **[1] Van jou →** naar', 'list(list_item(text(strong(C1 " Van jou →") " naar")))'],
		['**Van jou [1]**: tekst', 'paragraph(strong("Van jou " C1) ": tekst")'],
		['*[1]*', 'paragraph(em(C1))'],
		['a *[1] b* c', 'paragraph("a " em(C1 " b") " c")'],
		['**a [1] b**', 'paragraph(strong("a " C1 " b"))'],
		['**[1][2] x**', 'paragraph(strong(C1,2 " x"))'],
		['~~[1] oud~~', 'paragraph(del(C1 " oud"))'],
		['**Bold** [1] then', 'paragraph(strong("Bold") " " C1 " then")']
	])('%s renders the chip inside the emphasis', (markdown, expected) => {
		expect(render(markdown)).toBe(expected);
	});

	it('a space between an opening run and the marker leaves the run literal', () => {
		// The shape the v2 agent stream used to emit; the backend now writes the marker flush.
		expect(render('**' + ' [1] Van jou →** naar')).toBe('paragraph("** " C1 " Van jou →** naar")');
	});

	it.each([
		['Answer [1].', 'paragraph("Answer " C1 ".")'],
		['Answer [1, 2] and 【3】.', 'paragraph("Answer " C1,2 " and " C3 ".")'],
		['`**[1]**` and `[2]`', 'paragraph(codespan " and " codespan)'],
		['See [^1] here', 'paragraph("See [^1] here")']
	])('%s is unaffected', (markdown, expected) => {
		expect(render(markdown)).toBe(expected);
	});
});
