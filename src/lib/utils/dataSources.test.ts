import { describe, expect, it } from 'vitest';

import { dataSourceIds, requestedDataSources, toggleDataSource } from './dataSources';

const listed = [
	{ id: 'ds:publications', name: 'Publications', description: '' },
	{ id: 'ds:reports', name: 'Reports', description: 'Yearly' }
];

describe('data sources', () => {
	it('keeps ds: ids only, each once, in order', () => {
		expect(dataSourceIds(['ds:b', 'kb', 'ds:', null, 'ds:a', 'ds:b'])).toEqual(['ds:b', 'ds:a']);
		expect(dataSourceIds('ds:a')).toEqual([]);
	});

	it('sends the switched-on sources that are still listed', () => {
		expect(requestedDataSources(['ds:reports', 'ds:gone'], listed)).toEqual(['ds:reports']);
		expect(requestedDataSources(['ds:reports', 'ds:gone'], null)).toEqual([
			'ds:reports',
			'ds:gone'
		]);
		expect(requestedDataSources([], listed)).toEqual([]);
	});

	it('switches one source on and off', () => {
		expect(toggleDataSource([], 'ds:a')).toEqual(['ds:a']);
		expect(toggleDataSource(['ds:a', 'ds:b'], 'ds:a')).toEqual(['ds:b']);
	});
});
