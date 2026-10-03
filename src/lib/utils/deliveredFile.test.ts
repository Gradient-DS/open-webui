// [Gradient] Office references use chat authorization.
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
	type OfficeAttachment
} from './deliveredFile';

const office: OfficeAttachment = {
	type: 'office',
	name: 'deck.pptx',
	content_type: 'application/office',
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
	it('recognizes Office references including files without previews', () => {
		expect(isDeliveredFile(office)).toBe(true);
		expect(isDeliveredFile({ ...office, pages: 0 })).toBe(true);
		expect(isDeliveredFile({ ...office, thread_id: undefined })).toBe(true);
		expect(fileKind(office)).toBe('PPTX');
	});

	it.each([
		null,
		{},
		{ type: 'file', id: 'upload' },
		{ ...office, pages: [] },
		{ ...office, pages: -1 },
		{ ...office, pages: 1.5 },
		{ ...office, element_id: null },
		{ ...office, version: 0 },
		{ ...office, size: -1 }
	])('rejects an incomplete attachment: %j', (file) => {
		expect(isDeliveredFile(file)).toBe(false);
	});

	it('downloads through the encoded chat and element route with user auth', async () => {
		await downloadDeliveredFile({ ...office, element_id: 'element?#' }, 'chat/a');
		expect(fetch).toHaveBeenCalledWith('/api/v1/chats/chat%2Fa/office/element%3F%23/file', {
			headers: { Authorization: 'Bearer user-token' },
			credentials: 'include'
		});
		expect(saveAs).toHaveBeenCalledWith(expect.any(Blob), 'deck.pptx');
	});

	it('loads one-based page routes in parallel and preserves their order', async () => {
		const pending: ((response: Response) => void)[] = [];
		vi.mocked(fetch).mockImplementation(() => new Promise((resolve) => pending.push(resolve)));
		const loading = loadPages(office, 'chat');
		expect(fetch).toHaveBeenCalledTimes(2);
		expect(vi.mocked(fetch).mock.calls.map(([url]) => url)).toEqual([
			'/api/v1/chats/chat/office/element/page-1',
			'/api/v1/chats/chat/office/element/page-2'
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
		await expect(loadPages(office, 'chat')).rejects.toThrow('404');
		await expect(downloadDeliveredFile(office, 'chat')).rejects.toThrow('404');
		expect(URL.createObjectURL).not.toHaveBeenCalled();
		expect(saveAs).not.toHaveBeenCalled();
	});

	it('makes no page requests for a file without previews', async () => {
		expect(await loadPages({ ...office, pages: 0 }, 'chat')).toEqual([]);
		expect(fetch).not.toHaveBeenCalled();
	});

	it('a plain upload is not a delivered file', () => {
		expect(isDeliveredFile({ type: 'file', id: 'upload', pages: ['page'] })).toBe(false);
	});
});

describe('Office version selection', () => {
	it('opens the highest version of the requested file even when another file arrived later', () => {
		const contents = [
			{ file: { ...office, version: 3, element_id: 'v3' } },
			{ file: office },
			{ file: { ...office, version: 2, element_id: 'v2' } },
			{ file: { ...office, name: 'other.pptx', version: 4 } },
			{ file: { ...office, content_type: 'different', version: 5 } },
			{ markdown: 'Written document' }
		];
		expect(newestFileVersionIndex(contents, office)).toBe(0);
	});
	it('uses the last delivery when versions tie and leaves unrelated contents alone', () => {
		expect(
			newestFileVersionIndex(
				[{ file: office }, { file: { ...office, element_id: 'copy' } }],
				office
			)
		).toBe(1);
		expect(newestFileVersionIndex([{ file: { ...office, name: 'other.pptx' } }], office)).toBe(-1);
		expect(newestFileVersionIndex([], office)).toBe(-1);
	});
});
