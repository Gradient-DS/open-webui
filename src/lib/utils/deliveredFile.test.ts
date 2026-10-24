// [Gradient] Workspace file references use chat authorization.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const { saveAs } = vi.hoisted(() => ({ saveAs: vi.fn() }));
vi.mock('file-saver', () => ({ default: { saveAs } }));
vi.mock('$lib/constants', () => ({ WEBUI_API_BASE_URL: '/api/v1' }));

import {
	downloadDeliveredFile,
	fileKind,
	isDeliveredFile,
	loadPages,
	newestFileVersionIndex,
	type DeliveredFile
} from './deliveredFile';

const workspace: DeliveredFile = {
	type: 'workspace',
	name: 'deck.pptx',
	content_type: 'application/octet-stream',
	size: 42,
	thread_id: 'thread-secret',
	element_id: 'element',
	pages: 2,
	version: 1
};
beforeEach(() => {
	vi.stubGlobal('localStorage', { token: 'user-token' });
	vi.stubGlobal(
		'fetch',
		vi.fn().mockImplementation(async () => new Response(new Blob(['bytes'], { type: 'image/png' })))
	);
	vi.spyOn(URL, 'createObjectURL').mockImplementation(() => 'blob:preview');
});

afterEach(() => {
	vi.restoreAllMocks();
	vi.unstubAllGlobals();
	vi.clearAllMocks();
});

describe('delivered files', () => {
	it('recognizes workspace file references including files without previews', () => {
		expect(isDeliveredFile(workspace)).toBe(true);
		expect(isDeliveredFile({ ...workspace, pages: 0 })).toBe(true);
		expect(isDeliveredFile({ ...workspace, thread_id: undefined })).toBe(true);
		expect(fileKind(workspace)).toBe('PPTX');
	});

	it.each([
		null,
		{},
		{ type: 'file', id: 'upload' },
		{ ...workspace, pages: [] },
		{ ...workspace, pages: -1 },
		{ ...workspace, pages: 1.5 },
		{ ...workspace, element_id: null },
		{ ...workspace, version: 0 },
		{ ...workspace, size: -1 }
	])('rejects an incomplete attachment: %j', (file) => {
		expect(isDeliveredFile(file)).toBe(false);
	});

	it('downloads through the encoded chat and element route with user auth', async () => {
		await downloadDeliveredFile({ ...workspace, element_id: 'element?#' }, 'chat/a');
		expect(fetch).toHaveBeenCalledWith('/api/v1/chats/chat%2Fa/workspace/element%3F%23/file', {
			headers: { Authorization: 'Bearer user-token' },
			credentials: 'include'
		});
		expect(saveAs).toHaveBeenCalledWith(expect.any(Blob), 'deck.pptx');
	});

	it('loads one-based page routes in parallel and preserves their order', async () => {
		const pending: ((response: Response) => void)[] = [];
		vi.mocked(fetch).mockImplementation(() => new Promise((resolve) => pending.push(resolve)));
		const loading = loadPages(workspace, 'chat');
		expect(fetch).toHaveBeenCalledTimes(2);
		expect(vi.mocked(fetch).mock.calls.map(([url]) => url)).toEqual([
			'/api/v1/chats/chat/workspace/element/page-1',
			'/api/v1/chats/chat/workspace/element/page-2'
		]);
		for (const [, options] of vi.mocked(fetch).mock.calls) {
			expect(options?.headers).toEqual({ Authorization: 'Bearer user-token' });
		}
		pending[1](new Response('second'));
		pending[0](new Response('first'));
		await loading;
		const blobs = vi.mocked(URL.createObjectURL).mock.calls.map(([blob]) => blob as Blob);
		expect(await Promise.all(blobs.map((blob) => blob.text()))).toEqual(['first', 'second']);
	});

	it('does not create object URLs or save error responses', async () => {
		vi.mocked(fetch).mockResolvedValue(new Response('missing', { status: 404 }));
		await expect(loadPages(workspace, 'chat')).rejects.toThrow('404');
		await expect(downloadDeliveredFile(workspace, 'chat')).rejects.toThrow('404');
		expect(URL.createObjectURL).not.toHaveBeenCalled();
		expect(saveAs).not.toHaveBeenCalled();
	});

	it('makes no page requests for a file without previews', async () => {
		expect(await loadPages({ ...workspace, pages: 0 }, 'chat')).toEqual([]);
		expect(fetch).not.toHaveBeenCalled();
	});

	it('a plain upload is not a delivered file', () => {
		expect(isDeliveredFile({ type: 'file', id: 'upload', pages: ['page'] })).toBe(false);
	});
});

describe('Workspace file version selection', () => {
	const v2 = { ...workspace, element_id: 'v2', edits: workspace.element_id, version: 2 };
	const v3 = {
		...workspace,
		element_id: 'v3',
		edits: v2.element_id,
		version: 3,
		name: 'renamed.pptx'
	};
	it('follows the v1 → v2 → v3 lineage from any clicked version, even after a rename', () => {
		const contents = [{ file: v3 }, { file: workspace }, { file: v2 }, { markdown: 'Document' }];
		for (const file of [workspace, v2, v3]) expect(newestFileVersionIndex(contents, file)).toBe(0);
	});
	it('never mixes independent decks with the same name and content type', () => {
		const other = { ...workspace, element_id: 'other' };
		const otherV2 = { ...other, element_id: 'other-v2', edits: other.element_id, version: 2 };
		const contents = [{ file: workspace }, { file: v2 }, { file: other }, { file: otherV2 }];
		expect(newestFileVersionIndex(contents, workspace)).toBe(1);
		expect(newestFileVersionIndex(contents, other)).toBe(3);
	});
	it('uses the latest delivery when branches share a root and version', () => {
		const branch = { ...v2, element_id: 'branch-v2' };
		expect(newestFileVersionIndex([{ file: workspace }, { file: v2 }, { file: branch }], v2)).toBe(
			2
		);
	});
	it('can follow references to an ancestor missing from the visible contents', () => {
		expect(newestFileVersionIndex([{ file: v2 }, { file: v3 }], workspace)).toBe(1);
	});
	it('leaves unrelated contents alone and terminates malformed cycles', () => {
		expect(
			newestFileVersionIndex([{ file: { ...workspace, element_id: 'other' } }], workspace)
		).toBe(-1);
		expect(newestFileVersionIndex([], workspace)).toBe(-1);
		const cyclic = { ...v2, edits: 'v3' };
		expect(newestFileVersionIndex([{ file: cyclic }, { file: v3 }], cyclic)).toBe(-1);
	});
});
