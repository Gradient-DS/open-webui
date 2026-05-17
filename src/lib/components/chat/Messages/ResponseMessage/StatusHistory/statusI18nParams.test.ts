import { describe, expect, it } from 'vitest';
import { statusI18nParams } from './statusI18nParams';

describe('statusI18nParams', () => {
	it('passes through all non-reserved fields', () => {
		const status = {
			description: 'Searching {{collection_name}} for "{{query}}"...',
			action: 'tool_start',
			done: false,
			hidden: false,
			collection_name: 'jurisprudentie',
			query: 'arbeidsrecht'
		};
		expect(statusI18nParams(status)).toEqual({
			collection_name: 'jurisprudentie'
			// `query` is reserved — filtered out
		});
	});

	it('drops every reserved structural field', () => {
		const status = {
			description: 'x',
			action: 'a',
			done: true,
			hidden: true,
			urls: [],
			items: [],
			queries: [],
			count: 5,
			foo: 'bar',
			collection_name: 'kb-1'
		};
		expect(statusI18nParams(status)).toEqual({ foo: 'bar', collection_name: 'kb-1' });
	});

	it('returns empty object for status without params', () => {
		expect(statusI18nParams({ description: 'x' })).toEqual({});
		expect(statusI18nParams(null)).toEqual({});
		expect(statusI18nParams(undefined)).toEqual({});
	});
});
