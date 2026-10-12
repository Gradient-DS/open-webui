import { describe, expect, it } from 'vitest';

import { attachesLeft, hasChanges, tokenize, wordDiff } from './diff';

const show = (raw: string, clean: string) =>
	wordDiff(raw, clean).map(({ type, text }) =>
		type === 'same' ? text : `${type === 'added' ? '+' : '-'}${text}`
	);

describe('word diff', () => {
	it('splits words and punctuation, keeping inner apostrophes and hyphens', () => {
		expect(tokenize("Goedemorgen, zo'n e-mail.")).toEqual([
			'Goedemorgen',
			',',
			"zo'n",
			'e-mail',
			'.'
		]);
	});

	it('marks replaced words, removed fillers and added punctuation', () => {
		expect(show('Heedemorgen allemaal', 'Goedemorgen allemaal.')).toEqual([
			'-Heedemorgen',
			'+Goedemorgen',
			'allemaal',
			'+.'
		]);
		expect(show('eh we beginnen', 'We beginnen.')).toEqual(['-eh', '-we', '+We', 'beginnen', '+.']);
	});

	it('flags punctuation so it can be shown low-key', () => {
		const ops = wordDiff('ja', 'Ja.');
		expect(ops.find((op) => op.text === '.')?.punctuation).toBe(true);
		expect(ops.find((op) => op.text === 'Ja')?.punctuation).toBe(false);
	});

	it('returns only same ops when nothing changed', () => {
		expect(wordDiff('dank je', 'dank je').every((op) => op.type === 'same')).toBe(true);
		expect(wordDiff('', '')).toEqual([]);
	});

	it('knows which marks attach to the previous word', () => {
		expect(attachesLeft('.')).toBe(true);
		expect(attachesLeft('(')).toBe(false);
	});

	it('detects whether cleanup changed anything', () => {
		expect(hasChanges([{ raw: 'a', clean: 'a' }])).toBe(false);
		expect(hasChanges([{ raw: 'a', clean: '' }])).toBe(false);
		expect(hasChanges([{ raw: 'a', clean: 'A' }])).toBe(true);
	});
});
