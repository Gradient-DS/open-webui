import { describe, expect, it } from 'vitest';

import {
	MemoryPartStore,
	SafePartStore,
	interruptedActions,
	nextChunkSeq,
	nextPart,
	type PartStore
} from './parts';

const blob = (text: string) => new Blob([text], { type: 'audio/webm' });

describe('part store', () => {
	it('joins each part from its slices in order, whatever the write order', async () => {
		const store = new MemoryPartStore();
		await store.append('m1', 1, 1, blob('b'));
		await store.append('m1', 1, 0, blob('a'));
		await store.append('m1', 2, 0, blob('c'));
		await store.append('m2', 1, 0, blob('x'));
		const parts = await store.parts('m1');
		expect(parts.map(({ part }) => part)).toEqual([1, 2]);
		expect(await Promise.all(parts.map(({ blob }) => blob.text()))).toEqual(['ab', 'c']);
		expect(parts[0].blob.type).toBe('audio/webm');
		expect(nextPart(parts)).toBe(3);
		expect(nextPart([])).toBe(1);
	});

	it('removes only the given meeting', async () => {
		const store = new MemoryPartStore();
		await store.append('m1', 1, 0, blob('a'));
		await store.append('m2', 1, 0, blob('x'));
		await store.append('m1', 2, 0, blob('b'));
		await store.removePart('m1', 2);
		expect((await store.parts('m1')).map(({ part }) => part)).toEqual([1]);
		await store.remove('m1');
		expect(await store.parts('m1')).toEqual([]);
		expect(await store.parts('m2')).toHaveLength(1);
	});

	it('keeps working when storage fails', async () => {
		const broken: PartStore = {
			append: () => Promise.reject(new Error('quota')),
			parts: () => Promise.reject(new Error('blocked')),
			remove: () => Promise.reject(new Error('blocked')),
			removePart: () => Promise.reject(new Error('blocked'))
		};
		const errors: unknown[] = [];
		const store = new SafePartStore(broken, (error) => errors.push(error));
		await expect(store.append('m', 1, 0, blob('a'))).resolves.toBeUndefined();
		await expect(store.parts('m')).resolves.toEqual([]);
		await expect(store.remove('m')).resolves.toBeUndefined();
		expect(errors).toHaveLength(3);
	});
});

describe('interrupted meeting', () => {
	it('offers finishing from this device only with local parts, and live-only with live text', () => {
		expect(interruptedActions({ localParts: 2, liveParts: 3 })).toEqual([
			'finish_local',
			'resume',
			'finish_live'
		]);
		expect(interruptedActions({ localParts: 0, liveParts: 0 })).toEqual(['resume']);
		expect(interruptedActions({ localParts: 0, liveParts: 1 })).toEqual(['resume', 'finish_live']);
	});

	it('continues chunk numbering after the highest live seq', () => {
		expect(nextChunkSeq([{ seq: 1 }, { seq: 4 }, { seq: 2 }])).toBe(5);
		expect(nextChunkSeq(undefined)).toBe(1);
	});
});
