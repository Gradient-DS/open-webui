import { removeAllDetails, replaceOutsideCode } from '$lib/utils';
import { citationIds } from '$lib/utils/marked/citation-extension';
import { getOutputText, type OutputItem } from '../structuredOutput';
import type { DisplayCitation } from './reduceSources';

/**
 * [Gradient] A message's sources are the ones its own text cites. Each `[N]`
 * marker outside code resolves to the entry numbered N, as an inline click does
 * (`showSourceModal`), so the pill and the panel's question groups list exactly
 * what the answer cites, in stored order. Inline `[N]` keeps resolving against
 * the full stored list; a marker no entry carries names nothing.
 */
export function citedCitations(citations: DisplayCitation[], text: string): DisplayCitation[] {
	const cited = new Set<number>();
	replaceOutsideCode(text ?? '', (segment) => {
		citationIds(segment).forEach((id) => cited.add(id));
		return segment;
	});
	return citations.filter((citation) => cited.has(citation.n));
}

// [Gradient] The answer text a message shows: its output items, else its content without `<details>` blocks.
export function answerText(message: { content?: string; output?: OutputItem[] | null }): string {
	return getOutputText(message.output) || removeAllDetails(message.content ?? '');
}
