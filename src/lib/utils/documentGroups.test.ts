import { describe, expect, it } from 'vitest';
import { groupDocuments } from './documentGroups';

describe('groupDocuments', () => {
	it('keeps different documents apart and collects versions of one document', () => {
		const groups = groupDocuments([
			{ title: 'Concept onderzoekstoets' },
			{ title: 'Feit-bronmatrix.xlsx' },
			{ title: 'Concept onderzoekstoets' }
		]);
		expect(groups).toEqual([
			{ title: 'Concept onderzoekstoets', indices: [0, 2] },
			{ title: 'Feit-bronmatrix.xlsx', indices: [1] }
		]);
	});

	it('ignores surrounding whitespace in titles', () => {
		const groups = groupDocuments([{ title: 'Report' }, { title: ' Report ' }]);
		expect(groups).toEqual([{ title: 'Report', indices: [0, 1] }]);
	});

	it('treats every untitled document as its own document', () => {
		const groups = groupDocuments([{ title: '' }, { title: '' }]);
		expect(groups).toEqual([
			{ title: '', indices: [0] },
			{ title: '', indices: [1] }
		]);
	});
});
