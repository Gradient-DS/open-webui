// [Gradient] Markup the stream pipeline consumes instead of displaying.
//
// Tool/reasoning markers are hidden; documents become cards as soon as their
// opening tag arrives. Their raw bodies belong only in the document panel.
const STREAM_MARKUP_TAGS = [
	'details',
	'summary',
	'document',
	'source',
	'think',
	'thinking',
	'reason',
	'reasoning',
	'thought',
	'code_interpreter'
];

/**
 * Drop a pipeline tag the model is still typing.
 *
 * `<document title="Geschieden` is not a token until its `>` lands, so marked
 * emits it as literal text and it flashes in the message body for the frames the
 * rest of the tag takes to stream in. Cutting the unterminated tail closes that
 * window; the next delta brings the text back as real markup.
 *
 * Only the last `<` is considered, and only when what follows is a prefix of a
 * tag the pipeline consumes — so `a < b`, autolinks and ordinary HTML the user
 * meant to see are left alone.
 */
export const maskInFlightTag = (content: string): string => {
	if (typeof content !== 'string') return content;
	const open = content.lastIndexOf('<');
	// Nothing open, or it already closed: no tag is in flight.
	if (open === -1 || content.indexOf('>', open) !== -1) return content;

	const rest = content.slice(open + 1).replace(/^\//, '');
	const nameEnd = rest.search(/[\s/]/);
	const name = (nameEnd === -1 ? rest : rest.slice(0, nameEnd)).toLowerCase();
	// A name still being typed only has to be a prefix; once whitespace has
	// arrived the name is settled and has to match outright.
	const isMarkup = STREAM_MARKUP_TAGS.some((tag) =>
		nameEnd === -1 ? tag.startsWith(name) : tag === name
	);
	return isMarkup ? content.slice(0, open) : content;
};

// [Gradient] Where a gradual reveal of `text` may stop at or after `end`, so it never
// shows half of a construct that renders differently once complete: a <details>
// block (hidden tool and reasoning markers) shows whole or not at all, and a tag,
// a `**` or inline-code run, or a `[n]` citation shows once its closing part is
// revealed with it, or waits while the model is still writing that part.
const INLINE_PAIRS = ['**', '`'];
export const markupSafeEnd = (text: string, end: number): number => {
	const code = text.charCodeAt(end - 1);
	if (code >= 0xd800 && code <= 0xdbff) end += 1;

	const document = text.lastIndexOf('<document', end - 1);
	if (document !== -1) {
		const opening = text.slice(document).match(/^<document\b(?:[^>"']|"[^"]*"|'[^']*')*>/);
		if (!opening) return document;
		const close = text.indexOf('</document>', document + opening[0].length);
		if (close === -1) return text.length;
		if (end <= close + '</document>'.length) return close + '</document>'.length;
	}

	const details = text.lastIndexOf('<details', end - 1);
	if (details !== -1 && (document === -1 || details > text.indexOf('</document>', document))) {
		const close = text.indexOf('</details>', details);
		if (close === -1) return details;
		end = Math.max(end, close + '</details>'.length);
	}

	const tag = text.slice(0, end).search(/<\/?[a-zA-Z][^<>]*$/);
	if (tag !== -1) {
		const close = text.indexOf('>', end);
		return close === -1 ? tag : close + 1;
	}

	const lineStart = text.lastIndexOf('\n', end - 1) + 1;
	const line = text.slice(lineStart, end);
	if (!line.trimStart().startsWith('```')) {
		for (const mark of INLINE_PAIRS) {
			const opens = line.split(mark).length - 1;
			if (opens % 2 === 1) {
				const opener = lineStart + line.lastIndexOf(mark);
				const closer = text.indexOf(mark, end);
				return closer === -1 ? opener : closer + mark.length;
			}
		}
	}

	const bracket = line.lastIndexOf('[');
	if (bracket > line.lastIndexOf(']')) {
		const close = text.indexOf(']', end);
		if (close === -1) return lineStart + bracket;
		if (close - end < 16) return close + 1;
	}
	return end;
};
