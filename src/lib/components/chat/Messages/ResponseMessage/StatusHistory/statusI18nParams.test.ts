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

it('localizes mail order and address labels without changing addresses', () => {
	const labels: Record<string, string> = {
		'newest first': 'nieuwste eerst',
		From: 'Van',
		To: 'Aan',
		Cc: 'Cc'
	};
	const params = statusI18nParams(
		{
			action: 'search_mail',
			keywords: 'KNB',
			matches: '143',
			options: 'English fallback',
			mail_options: [
				{ label: 'newest first', value: '' },
				{ label: 'From', value: '@knb.nl' },
				{ label: 'To', value: 'lex@example.test' },
				{ label: 'Cc', value: 'copy@example.test' }
			]
		},
		(key) => labels[key]
	);
	expect(params.options).toBe(
		'nieuwste eerst; Van: @knb.nl; Aan: lex@example.test; Cc: copy@example.test'
	);
	expect(params.mail_options).toBeUndefined();
});
