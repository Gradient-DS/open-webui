/**
 * Decides how the relevance score of a citation chunk is rendered:
 * as a coloured percentage badge, as a raw score, or not at all.
 *
 * Both predicates run over *every* chunk of *every* citation in a message,
 * so one odd score flips the whole panel — that is deliberate: a message
 * must not mix percentage badges with raw scores.
 *
 * `undefined` entries are holes, not data. `reduceSources` pushes
 * `document` / `metadata` / `distances` unconditionally so the three
 * arrays stay index-aligned, which means every chunk of a source that
 * carried no `distances` at all (whole-document reads — `read_document`,
 * `summarize`, `fetch_url` — where soev-agents omits the field) leaves an
 * `undefined` behind. Those holes carry no information about the scale of
 * the real scores, so they are filtered out before either predicate looks
 * at the numbers. Counting them as "not a percentage" made a single
 * whole-document citation downgrade every scored chunk in the message to a
 * grey raw score.
 */

interface ScoredCitation {
	distances?: (number | undefined)[];
}

const definedDistances = (sources: ScoredCitation[]): number[] =>
	sources.flatMap((citation) => citation.distances ?? []).filter((d) => d !== undefined);

/**
 * Whether to show relevance at all. Suppressed when the scores carry no
 * signal: none present, or a single outlier against an otherwise uniform
 * set (one score outside [-1, 1] among in-range ones, or vice versa) —
 * that shape means the numbers are not on one comparable scale.
 */
export function calculateShowRelevance(sources: ScoredCitation[]): boolean {
	const distances = definedDistances(sources);
	const inRange = distances.filter((d) => d >= -1 && d <= 1).length;
	const outOfRange = distances.length - inRange;

	if (distances.length === 0) {
		return false;
	}

	if (
		(inRange === distances.length - 1 && outOfRange === 1) ||
		(outOfRange === distances.length - 1 && inRange === 1)
	) {
		return false;
	}

	return true;
}

/**
 * Whether the scores can be read as percentages — true only when every
 * real score sits in [-1, 1]. Anything else (reranker logits, BM25 scores)
 * renders as a raw number instead.
 */
export function shouldShowPercentage(sources: ScoredCitation[]): boolean {
	const distances = definedDistances(sources);
	return distances.length > 0 && distances.every((d) => d >= -1 && d <= 1);
}
