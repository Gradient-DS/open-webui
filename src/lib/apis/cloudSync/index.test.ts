import { afterEach, describe, expect, it, vi } from 'vitest';
import * as cloudSync from './index';

vi.mock('$lib/constants', () => ({ WEBUI_API_BASE_URL: '/api/v1' }));
afterEach(() => vi.unstubAllGlobals());

function respond(body: unknown, status = 200) {
	const fetch = vi
		.fn()
		.mockImplementation(
			async () => new Response(status === 204 ? null : JSON.stringify(body), { status })
		);
	vi.stubGlobal('fetch', fetch);
	return fetch;
}

const token = 'invalid-test-session';

describe('cloud-sync thin router', () => {
	it('creates a connection with only the provider and returns the authorize URL', async () => {
		const result = { connection_id: 'c', authorize_url: 'https://provider.invalid/authorize' };
		const fetch = respond(result);
		expect(await cloudSync.createConnection(token, 'onedrive')).toEqual(result);
		expect(fetch).toHaveBeenCalledWith(
			'/api/v1/cloud-sync/connections',
			expect.objectContaining({
				method: 'POST',
				body: JSON.stringify({ provider: 'onedrive' }),
				headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' }
			})
		);
	});

	it.each([undefined, 60])(
		'registers scope with optional cadence %s and no provider token',
		async (cadence) => {
			const fetch = respond({ id: 's' });
			const form: cloudSync.ScheduleForm = {
				connection_id: 'c',
				kind: 'content',
				label: 'Reports',
				path: '/Team/Reports',
				...(cadence === undefined ? {} : { cadence_minutes: cadence }),
				scope: { drive_id: 'd', item_id: 'folder', include_descendants: true, single_file: false }
			};
			await cloudSync.createSchedule(token, 'kb/one', form);
			expect(fetch).toHaveBeenCalledWith(
				'/api/v1/cloud-sync/knowledge/kb%2Fone/schedules',
				expect.objectContaining({ method: 'POST', body: JSON.stringify(form) })
			);
		}
	);

	it('reads connection and schedule lifecycle including run state', async () => {
		const result = {
			schedules: [
				{
					id: 's',
					document_count: 12,
					last_run: { id: 'run', outcome: null, counts: { fetched: 3 } },
					connection: { lifecycle: 'suspended:reauth' }
				}
			]
		};
		const fetch = respond(result);
		expect(await cloudSync.getSyncStatus(token, 'kb')).toEqual(result);
		expect(fetch.mock.calls[0][0]).toBe('/api/v1/cloud-sync/knowledge/kb/sync');
		await cloudSync.listConnections(token);
		await cloudSync.getConnection(token, 'c/one');
		await cloudSync.authorizeConnection(token, 'c/one');
		expect(fetch.mock.calls.slice(1).map(([path, init]) => [path, init.method])).toEqual([
			['/api/v1/cloud-sync/connections', 'GET'],
			['/api/v1/cloud-sync/connections/c%2Fone', 'GET'],
			['/api/v1/cloud-sync/connections/c%2Fone/authorize', 'POST']
		]);
	});

	it('reads distinct knowledge base usage from the encoded connection route', async () => {
		const result = { knowledge_ids: ['kb-1', 'kb-2'] };
		const fetch = respond(result);
		expect(await cloudSync.getConnectionUsage(token, 'c/one')).toEqual(result);
		expect(fetch).toHaveBeenCalledWith(
			'/api/v1/cloud-sync/connections/c%2Fone/usage',
			expect.objectContaining({ method: 'GET', headers: { Authorization: `Bearer ${token}` } })
		);
	});

	it('reads named skipped files and preserves content failure codes', async () => {
		const result = [
			{ source_id: 'empty', name: 'Empty.pdf', code: 'empty_content' },
			{ source_id: 'broken', name: 'Broken.pdf', code: 'processing_failed' },
			{ source_id: 'slow', name: 'Slow.pdf', code: 'timed_out' }
		];
		const fetch = respond(result);
		expect(await cloudSync.getSkippedItems(token, 'kb', 'content/run')).toEqual(result);
		expect(fetch).toHaveBeenCalledWith(
			'/api/v1/cloud-sync/knowledge/kb/schedules/content%2Frun/skipped',
			expect.objectContaining({ method: 'GET', headers: { Authorization: `Bearer ${token}` } })
		);
	});

	it('handles run jobs and bodyless schedule and revoke responses', async () => {
		respond({ job_id: 'job' }, 201);
		expect(await cloudSync.runSchedule(token, 'kb', 's')).toEqual({ job_id: 'job' });
		const fetch = respond(null, 204);
		for (const action of [
			cloudSync.cancelSchedule,
			cloudSync.suspendSchedule,
			cloudSync.resumeSchedule,
			cloudSync.deleteSchedule
		]) {
			expect(await action(token, 'kb', 's/one')).toBeUndefined();
		}
		expect(await cloudSync.revokeConnection(token, 'c/one')).toBeUndefined();
		expect(fetch.mock.calls.map(([path, init]) => [path, init.method])).toEqual([
			['/api/v1/cloud-sync/knowledge/kb/schedules/s%2Fone/cancel', 'POST'],
			['/api/v1/cloud-sync/knowledge/kb/schedules/s%2Fone/suspend', 'POST'],
			['/api/v1/cloud-sync/knowledge/kb/schedules/s%2Fone/resume', 'POST'],
			['/api/v1/cloud-sync/knowledge/kb/schedules/s%2Fone', 'DELETE'],
			['/api/v1/cloud-sync/connections/c%2Fone', 'DELETE']
		]);
	});

	it.each([
		[403, 'policy_forbids'],
		[404, 'connection_not_found'],
		[409, 'connection_pending'],
		[409, 'schedule_exists'],
		[409, 'run_active'],
		[429, 'run_too_soon'],
		[422, 'invalid_field']
	])('preserves a %i problem code and constraint', async (status, code) => {
		respond({ detail: { code, detail: 'Refused', constraint: 'provider' } }, Number(status));
		await expect(cloudSync.createConnection(token, 'google_drive')).rejects.toMatchObject({
			status,
			code,
			message: 'Refused',
			constraint: 'provider'
		});
	});

	it('preserves an unwrapped soev problem code', async () => {
		respond({ code: 'run_too_soon', detail: 'Try later' }, 429);
		await expect(cloudSync.runSchedule(token, 'kb', 's')).rejects.toMatchObject({
			status: 429,
			code: 'run_too_soon',
			message: 'Try later'
		});
	});

	it('handles non-JSON errors without exposing the response body', async () => {
		vi.stubGlobal(
			'fetch',
			vi.fn().mockResolvedValue(new Response('upstream failure', { status: 502 }))
		);
		await expect(cloudSync.listConnections(token)).rejects.toMatchObject({
			status: 502,
			code: 'request_failed',
			message: 'HTTP 502'
		});
	});
});
