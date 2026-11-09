import { describe, expect, it } from 'vitest';

import {
	provisionedKnowledgeIds,
	requestedProvisionedKnowledge,
	toggleProvisionedKnowledge
} from './provisionedKnowledge';

const listed = [
	{ id: 'pk-publications', name: 'Publications', description: '' },
	{ id: 'pk-reports', name: 'Reports', description: 'Yearly' }
];

describe('provisioned knowledge', () => {
	it('keeps nonempty opaque keys once in order', () => {
		expect(provisionedKnowledgeIds(['b', '', null, 3, 'a', 'b'])).toEqual(['b', 'a']);
		expect(provisionedKnowledgeIds('pk-a')).toEqual([]);
	});

	it('sends the switched-on sources that are still listed', () => {
		expect(requestedProvisionedKnowledge(['pk-reports', 'pk-gone'], listed)).toEqual([
			'pk-reports'
		]);
		expect(requestedProvisionedKnowledge(['pk-reports', 'pk-gone'], null)).toEqual([
			'pk-reports',
			'pk-gone'
		]);
		expect(requestedProvisionedKnowledge([], listed)).toEqual([]);
	});

	it('switches one source on and off', () => {
		expect(toggleProvisionedKnowledge([], 'pk-a')).toEqual(['pk-a']);
		expect(toggleProvisionedKnowledge(['pk-a', 'pk-b'], 'pk-a')).toEqual(['pk-b']);
	});
});
