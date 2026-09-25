import { afterEach, describe, expect, it, vi } from 'vitest';
import { get } from 'svelte/store';
import {
	notifyWorkspaceMutation,
	registerWorkspaceCountLoaders,
	setWorkspaceCount,
	workspaceCounts
} from './workspace-counts';

const loaders = () => ({
	models: vi.fn(async () => ({ total: 1 })),
	knowledge: vi.fn(async () => ({ total: 2 })),
	prompts: vi.fn(async () => ({ total: 3 })),
	skills: vi.fn(async () => ({ total: 4 })),
	tools: vi.fn(async () => [{ id: 'tool' }])
});
let dispose: (() => void) | undefined;
afterEach(() => dispose?.());

describe('workspace counts', () => {
	it('loads each section once and refreshes only the mutated section', async () => {
		const load = loaders();
		dispose = registerWorkspaceCountLoaders(load);
		await Promise.resolve();
		expect(get(workspaceCounts)).toEqual({
			models: 1,
			knowledge: 2,
			prompts: 3,
			skills: 4,
			tools: 1
		});
		for (const section of ['models', 'knowledge', 'prompts', 'skills', 'tools'] as const) {
			notifyWorkspaceMutation(section);
			await Promise.resolve();
			expect(load[section]).toHaveBeenCalledTimes(2);
		}
	});

	it('does not overwrite a page total with a slower layout response', async () => {
		dispose = registerWorkspaceCountLoaders(loaders());
		setWorkspaceCount('knowledge', 8);
		await Promise.resolve();
		expect(get(workspaceCounts).knowledge).toBe(8);
	});

	it('discards requests superseded by a mutation', async () => {
		let resolve!: (result: { total: number }) => void;
		const load = loaders();
		load.knowledge.mockImplementationOnce(
			() =>
				new Promise((done) => {
					resolve = done;
				})
		);
		dispose = registerWorkspaceCountLoaders(load);
		notifyWorkspaceMutation('knowledge');
		await Promise.resolve();
		resolve({ total: 99 });
		await Promise.resolve();
		expect(get(workspaceCounts).knowledge).toBe(2);
	});

	it('ignores failed refreshes and stops refreshing after unmount', async () => {
		const load = loaders();
		dispose = registerWorkspaceCountLoaders(load);
		await Promise.resolve();
		load.models.mockRejectedValueOnce(new Error('offline'));
		notifyWorkspaceMutation('models');
		await Promise.resolve();
		expect(get(workspaceCounts).models).toBe(1);
		dispose();
		notifyWorkspaceMutation('models');
		expect(load.models).toHaveBeenCalledTimes(2);
	});

	it('discards responses from an unmounted layout', async () => {
		dispose = registerWorkspaceCountLoaders(loaders());
		dispose();
		setWorkspaceCount('models', 7);
		await Promise.resolve();
		expect(get(workspaceCounts).models).toBe(7);
	});
});
