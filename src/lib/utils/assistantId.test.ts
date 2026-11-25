import { describe, expect, it, vi } from 'vitest';
import { createWithFreeId, isIdTaken, slugify, uniqueSlug } from './assistantId';

const TAKEN = 'Uh-oh! This model id is already registered. Please choose another model id string.';

describe('uniqueSlug', () => {
	it('slugs the name', () => {
		expect(slugify('  Raadsinformatie Assistent! ')).toBe('raadsinformatie-assistent');
		expect(uniqueSlug('Raadsinformatie Assistent', ['other'])).toBe('raadsinformatie-assistent');
	});

	it('suffixes a slug the user can see is taken', () => {
		expect(uniqueSlug('Helper', ['helper'])).toMatch(/^helper-[0-9a-f]{6}$/);
	});

	it('leaves a name without letters or digits empty', () => {
		expect(uniqueSlug('!!!', [])).toBe('');
	});
});

describe('isIdTaken', () => {
	it('knows the server refusal', () => {
		expect(isIdTaken(TAKEN)).toBe(true);
		expect(isIdTaken('Unauthorized')).toBe(false);
		expect(isIdTaken(undefined)).toBe(false);
	});
});

describe('createWithFreeId', () => {
	it('creates with the plain id when it is free', async () => {
		const info = { id: 'helper' };
		const create = vi.fn(async (model: { id: string }) => ({ id: model.id }));
		expect(await createWithFreeId(info, create)).toEqual({ id: 'helper' });
		expect(create).toHaveBeenCalledTimes(1);
	});

	it('retries once with a suffixed id when an unseen model has it', async () => {
		const info = { id: 'helper' };
		const create = vi.fn(async (model: { id: string }) => {
			if (model.id === 'helper') throw TAKEN;
			return { id: model.id };
		});
		const res = await createWithFreeId(info, create);
		expect(res.id).toMatch(/^helper-[0-9a-f]{6}$/);
		expect(info.id).toBe(res.id);
		expect(create).toHaveBeenCalledTimes(2);
	});

	it('passes any other refusal on without a retry', async () => {
		const create = vi.fn(async () => {
			throw 'Unauthorized';
		});
		await expect(createWithFreeId({ id: 'helper' }, create)).rejects.toBe('Unauthorized');
		expect(create).toHaveBeenCalledTimes(1);
	});
});
