import { sourceKey, type RawSource } from '../Citations/reduceSources';

/**
 * [Gradient] The label of each `[N]` at index N - 1: one per document, keyed as `reduceSources` keys its
 * entries, so a chip names the source a click opens. Two documents with one name keep their own numbers.
 */
export function sourceLabels(
	sources: RawSource[] | null | undefined,
	citationsOff = false
): string[] {
	const labels = new Map<string, string>();
	for (const source of sources ?? []) {
		(source?.document ?? []).forEach((_, index) => {
			const metadata = source.metadata?.[index];
			const key = sourceKey(source, metadata);
			if (labels.has(key)) return;
			const id = metadata?.source ?? 'N/A';
			labels.set(
				key,
				citationsOff
					? 'N/A'
					: metadata?.name ||
							(id.startsWith('http://') || id.startsWith('https://')
								? id
								: (source.source?.name ?? id))
			);
		});
	}
	return [...labels.values()];
}

/** [Gradient] A grouped chip's label: its first marker with a source, and how many more it cites. A marker past the
 * end of the sources (an answer stored before its sources were) names nothing; `null` when none has a source. */
export function chipLabel(ids: number[], labels: string[]): { title: string; more: number } | null {
	const known = ids.filter((id) => labels[id - 1] !== undefined);
	return known.length ? { title: labels[known[0] - 1], more: known.length - 1 } : null;
}
