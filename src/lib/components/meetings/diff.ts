// [Gradient] Vergadering: what the cleanup pass changed, word by word (LCS over tokens).

export type DiffOp = { type: 'same' | 'removed' | 'added'; text: string; punctuation: boolean };

/** Words (letters, digits, apostrophes, inner hyphens) and single punctuation marks. */
export const tokenize = (text: string): string[] =>
	text.match(/[\p{L}\p{N}]+(?:['’-][\p{L}\p{N}]+)*|[^\s\p{L}\p{N}]/gu) ?? [];

const isPunctuation = (token: string) => !/[\p{L}\p{N}]/u.test(token);

/** Diff of `raw` → `clean`; equal tokens are `same`, raw-only `removed`, clean-only `added`. */
export const wordDiff = (raw: string, clean: string): DiffOp[] => {
	const a = tokenize(raw);
	const b = tokenize(clean);
	// lcs[i][j] = length of the LCS of a[i..] and b[j..]
	const lcs = Array.from({ length: a.length + 1 }, () => new Array<number>(b.length + 1).fill(0));
	for (let i = a.length - 1; i >= 0; i--) {
		for (let j = b.length - 1; j >= 0; j--) {
			lcs[i][j] = a[i] === b[j] ? lcs[i + 1][j + 1] + 1 : Math.max(lcs[i + 1][j], lcs[i][j + 1]);
		}
	}
	const ops: DiffOp[] = [];
	const push = (type: DiffOp['type'], text: string) =>
		ops.push({ type, text, punctuation: isPunctuation(text) });
	let i = 0;
	let j = 0;
	while (i < a.length && j < b.length) {
		if (a[i] === b[j]) {
			push('same', a[i]);
			i++;
			j++;
		} else if (lcs[i + 1][j] >= lcs[i][j + 1]) {
			push('removed', a[i++]);
		} else {
			push('added', b[j++]);
		}
	}
	while (i < a.length) push('removed', a[i++]);
	while (j < b.length) push('added', b[j++]);
	return ops;
};

/** Whether a token is written without a space before it (closing punctuation). */
export const attachesLeft = (token: string): boolean => /^[.,!?;:)\]}%…»”’]$/u.test(token);

export const hasChanges = (segments: { raw: string; clean: string }[]): boolean =>
	segments.some((segment) => (segment.clean || segment.raw) !== segment.raw);
