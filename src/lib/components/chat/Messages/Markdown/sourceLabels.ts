import { sourceKey, type RawSource } from '../Citations/reduceSources';

/**
 * [Gradient] The label of each `[N]` at index N - 1: one per document, keyed and numbered as `reduceSources`
 * keys and numbers its entries (the agent's `n`, else position), so a chip names the source a click opens. Two
 * documents with one name keep their own numbers.
 */
export function sourceLabels(
	sources: RawSource[] | null | undefined,
	citationsOff = false
): string[] {
	const labels = new Map<string, { label: string; n?: number }>();
	for (const source of sources ?? []) {
		(source?.document ?? []).forEach((_, index) => {
			const metadata = source.metadata?.[index];
			const key = sourceKey(source, metadata);
			const known = labels.get(key);
			if (known) {
				if (source.n !== undefined) known.n = source.n;
				return;
			}
			const id = metadata?.source ?? 'N/A';
			const label = citationsOff
				? 'N/A'
				: metadata?.name ||
					(id.startsWith('http://') || id.startsWith('https://')
						? id
						: (source.source?.name ?? id));
			labels.set(key, { label, n: source.n });
		});
	}
	const numbered: string[] = [];
	[...labels.values()].forEach(({ label, n }, index) => {
		numbered[(n ?? index + 1) - 1] = label;
	});
	return numbered;
}

/** [Gradient] A grouped chip's label: its first marker with a source, and how many more it cites. A marker no source
 * carries (an answer stored before its sources were) names nothing; `null` when none has a source. */
export function chipLabel(ids: number[], labels: string[]): { title: string; more: number } | null {
	const known = ids.filter((id) => labels[id - 1] !== undefined);
	return known.length ? { title: labels[known[0] - 1], more: known.length - 1 } : null;
}
