// [Gradient] Markup the stream pipeline consumes instead of displaying.
//
// The backend lifts these tags out of the assistant text into output items
// (DEFAULT_REASONING_TAGS / DEFAULT_CODE_INTERPRETER_TAGS /
// DEFAULT_DOCUMENT_WRITER_TAGS in backend/open_webui/utils/middleware.py), and
// the agent emits <details type="tool_calls"> anchors that StatusHistory renders
// in MarkdownTokens' place. Every one of them is invisible once complete — which
// is exactly why the half-typed form is the only form a user ever sees.
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
