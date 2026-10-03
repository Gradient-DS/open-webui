import { describe, expect, it } from 'vitest';

import { describeBlock } from './statusSummary';

const DUTCH: Record<string, string> = {
	thought: 'nagedacht',
	'searched the web': 'op het internet gezocht',
	'read {{count}} websites': '{{count}} websites gelezen',
	'read a document': 'een document gelezen',
	'created a file': 'een bestand gemaakt',
	and: 'en'
};

const t = (key: string, options?: Record<string, unknown>) =>
	(DUTCH[key] ?? key).replace('{{count}}', String(options?.count ?? ''));

describe('describeBlock', () => {
	it('names the thinking and the websites read', () => {
		const items = [
			{ kind: 'reasoning' },
			{ action: 'fetch' },
			{ action: 'fetch' },
			{ action: 'fetch' }
		];
		expect(describeBlock(items, t)).toBe('Nagedacht en 3 websites gelezen');
	});

	it('lists several kinds of steps in order', () => {
		const items = [{ kind: 'reasoning' }, { action: 'web_search' }, { action: 'open_document' }];
		expect(describeBlock(items, t)).toBe(
			'Nagedacht, op het internet gezocht en een document gelezen'
		);
	});

	it('counts an Office agent steps as the making of its file', () => {
		const items = [{ action: 'create_office_file' }, { action: 'run' }, { action: 'preview' }];
		expect(describeBlock(items, t)).toBe('Een bestand gemaakt');
	});

	it('leaves a block of one step to its own line', () => {
		expect(describeBlock([{ kind: 'reasoning' }], t)).toBeNull();
	});
});
