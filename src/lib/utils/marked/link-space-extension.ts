// [Gradient] A link whose web address holds spaces, such as a OneDrive or SharePoint
// document the agent links to (`[deck](https://…/5. Projects/deck.pptx)`), is not a
// link in Markdown: marked stops at the first space and shows broken text. This
// accepts such an http(s) address up to the closing parenthesis, one level of
// parentheses inside it included, and links to it with its spaces encoded. A
// destination with a quote (a link title) or no space is left to marked.
const SPACED_LINK = /^\[([^[\]\n]*)\]\((https?:\/\/(?:[^\s()<>"]|[ \t]|\([^()\n]*\))+)\)/;

export const linkSpaceExtension = {
	tokenizer: {
		link(src: string) {
			const match = SPACED_LINK.exec(src);
			const href = match?.[2].trim();
			if (!match || !href || !/[ \t]/.test(href)) {
				return false;
			}
			return {
				type: 'link',
				raw: match[0],
				href: href.replace(/[ \t]/g, '%20'),
				title: null,
				text: match[1],
				tokens: this.lexer.inlineTokens(match[1])
			};
		}
	}
};
