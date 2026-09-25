import { describe, expect, it } from 'vitest';
import { createKnowledgeListCache, type KnowledgeListPage } from './listCache';

const page: KnowledgeListPage = { items: [{ id: 'kb', name: 'KB', updated_at: 1 }], total: 1 };

describe('knowledge first-page snapshots', () => {
	it('isolates users, queries and filters', () => {
		const cache = createKnowledgeListCache();
		const key = JSON.stringify(['user-a', 'token', '', 'mine', 'local', '', 'name', 'asc']);
		cache.set(key, page, cache.revision);
		expect(cache.get(key)).toEqual(page);
		for (let index = 0; index < 8; index++) {
			const parts: string[] = JSON.parse(key);
			parts[index] = 'different';
			expect(cache.get(JSON.stringify(parts))).toBeUndefined();
		}
	});

	it('replaces snapshots on revalidation without retaining component mutations', () => {
		const cache = createKnowledgeListCache();
		cache.set('key', page, cache.revision);
		cache.get('key')!.items[0].name = 'edited';
		expect(cache.get('key')).toEqual(page);
		cache.set('key', { items: [], total: 0 }, cache.revision);
		expect(cache.get('key')).toEqual({ items: [], total: 0 });
	});

	it('invalidates all filters and rejects responses started before create/delete', () => {
		const cache = createKnowledgeListCache();
		const revision = cache.revision;
		cache.set('all', page, revision);
		cache.set('mine', page, revision);
		cache.invalidate();
		cache.set('all', page, revision);
		expect(cache.get('all')).toBeUndefined();
		expect(cache.get('mine')).toBeUndefined();
		cache.set('all', page, cache.revision);
		expect(cache.get('all')).toEqual(page);
	});

	it('bounds retained first pages', () => {
		const cache = createKnowledgeListCache(2);
		for (const key of ['a', 'b', 'c']) cache.set(key, page, cache.revision);
		expect(cache.get('a')).toBeUndefined();
		expect(cache.get('b')).toEqual(page);
		expect(cache.get('c')).toEqual(page);
	});
});
