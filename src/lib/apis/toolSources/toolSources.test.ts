import { afterEach, expect, it, vi } from 'vitest';
import { availableToolSourceIds, getToolSources, type ToolSource } from './index';

const source: ToolSource = {
	id: '00000000-0000-4000-8000-000000000001',
	kind: 'openapi',
	name: 'Fable weather',
	group_name: 'Fable',
	countries: ['NL']
};
afterEach(() => vi.unstubAllGlobals());

it('discovers as the user with the configured default agent', async () => {
	const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({ data: [source] })));
	vi.stubGlobal('fetch', fetch);
	expect(await getToolSources('fictional-token')).toEqual([source]);
	expect(fetch).toHaveBeenCalledWith(expect.stringMatching(/\/tool-sources$/), {
		headers: { Accept: 'application/json', authorization: 'Bearer fictional-token' }
	});
});
it('encodes an explicit agent and accepts an empty list', async () => {
	const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({ data: [] })));
	vi.stubGlobal('fetch', fetch);
	expect(await getToolSources('fictional-token', 'fictional agent&x')).toEqual([]);
	expect(fetch.mock.calls[0][0]).toMatch(/\?agent=fictional\+agent%26x$/);
});
it.each([401, 403, 404, 503])(
	'rejects unavailable routes without exposing details (%s)',
	async (status) => {
		vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('private details', { status })));
		await expect(getToolSources('fictional-token')).rejects.toThrow(`HTTP ${status}`);
	}
);
it('drops stale selections, deduplicates and caps the selection at eight', () => {
	expect(availableToolSourceIds(['gone', source.id, source.id], [source])).toEqual([source.id]);
	expect(availableToolSourceIds([source.id], [])).toEqual([]);
	const sources = Array.from({ length: 9 }, (_, index) => ({ ...source, id: `${index}` }));
	expect(
		availableToolSourceIds(
			sources.map(({ id }) => id),
			sources
		)
	).toHaveLength(8);
});
