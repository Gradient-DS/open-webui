import { describe, it, expect } from 'vitest';
import { sourceByRootDirectoryId } from './sourceMap';

describe('sourceByRootDirectoryId', () => {
	it('maps folder-like sources by their materialized root directory', () => {
		const sources = [
			{ item_id: 'od-1', name: 'Docs', type: 'folder', root_directory_id: 'dir-a' },
			{ item_id: 'od-2', name: 'Reports', type: 'folder', root_directory_id: 'dir-b' }
		];
		const map = sourceByRootDirectoryId(sources);
		expect(map.get('dir-a')?.item_id).toBe('od-1');
		expect(map.get('dir-b')?.name).toBe('Reports');
		expect(map.size).toBe(2);
	});

	it('skips file-type sources (no wrapper directory)', () => {
		const sources = [
			{ item_id: 'od-file', name: 'Salaris.xlsx', type: 'file' },
			{ item_id: 'od-dir', name: 'Docs', type: 'folder', root_directory_id: 'dir-a' }
		];
		const map = sourceByRootDirectoryId(sources);
		expect(map.size).toBe(1);
		expect(map.get('dir-a')?.item_id).toBe('od-dir');
	});

	it('skips sources whose root directory was never stamped', () => {
		const map = sourceByRootDirectoryId([{ item_id: 'od-1', name: 'Docs', type: 'folder' }]);
		expect(map.size).toBe(0);
	});

	it('handles null/undefined/empty input', () => {
		expect(sourceByRootDirectoryId(null).size).toBe(0);
		expect(sourceByRootDirectoryId(undefined).size).toBe(0);
		expect(sourceByRootDirectoryId([]).size).toBe(0);
	});
});
