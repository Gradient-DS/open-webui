import { describe, it, expect, vi, beforeEach } from 'vitest';
import { searchKnowledgeFilesById } from './index';

// Stub $app/environment so that WEBUI_API_BASE_URL resolves to /api/v1
vi.mock('$app/environment', () => ({ browser: false, dev: false }));

const okPayload = { items: [], total: 0 };

function stubFetch(payload = okPayload) {
	return vi.fn().mockResolvedValue({
		ok: true,
		json: async () => payload
	} as unknown as Response);
}

describe('searchKnowledgeFilesById', () => {
	beforeEach(() => {
		vi.stubGlobal('fetch', stubFetch());
	});

	it('appends metadata_only=true to the request URL when metadataOnly is true', async () => {
		const fetchSpy = stubFetch();
		vi.stubGlobal('fetch', fetchSpy);

		await searchKnowledgeFilesById('tok', 'kb-1', null, null, null, null, 1, null, true);

		expect(fetchSpy).toHaveBeenCalledOnce();
		const calledUrl: string = fetchSpy.mock.calls[0][0];
		expect(calledUrl).toContain('metadata_only=true');
	});

	it('does NOT append metadata_only when metadataOnly is false (default)', async () => {
		const fetchSpy = stubFetch();
		vi.stubGlobal('fetch', fetchSpy);

		await searchKnowledgeFilesById('tok', 'kb-1');

		expect(fetchSpy).toHaveBeenCalledOnce();
		const calledUrl: string = fetchSpy.mock.calls[0][0];
		expect(calledUrl).not.toContain('metadata_only');
	});

	it('includes the knowledge id in the request URL', async () => {
		const fetchSpy = stubFetch();
		vi.stubGlobal('fetch', fetchSpy);

		await searchKnowledgeFilesById('tok', 'my-kb-id', null, null, null, null, 1, null, true);

		const calledUrl: string = fetchSpy.mock.calls[0][0];
		expect(calledUrl).toContain('/knowledge/my-kb-id/files');
	});

	it('includes the limit param when provided', async () => {
		const fetchSpy = stubFetch();
		vi.stubGlobal('fetch', fetchSpy);

		await searchKnowledgeFilesById('tok', 'kb-1', null, null, null, null, 1, 10000, true);

		const calledUrl: string = fetchSpy.mock.calls[0][0];
		expect(calledUrl).toContain('limit=10000');
		expect(calledUrl).toContain('metadata_only=true');
	});
});
