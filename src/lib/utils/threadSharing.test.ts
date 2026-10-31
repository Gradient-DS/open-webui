import { afterEach, describe, expect, it, vi } from 'vitest';
import { groupAudience, personAudience, sharingAPI } from './threadSharing';
import { searchUsers } from '$lib/apis/users';

const id = '70c51d1b-8310-4649-89bd-084764d104f6';

afterEach(() => vi.unstubAllGlobals());

describe('thread share recipients', () => {
	it('uses the bound platform person ref instead of the local user id', () => {
		const user = { id: 'local-user-id', person_id: `platform:user:${id}` };
		expect(personAudience(user)).toEqual({ kind: 'person', id: `platform:user:${id}` });
	});

	it.each([
		undefined,
		null,
		'',
		id,
		`owui:user:${id}`,
		`platform:person:${id}`,
		`platform:user:${id.toUpperCase()}`,
		'platform:user:not-a-uuid'
	])('excludes an unbound or noncanonical person: %s', (person_id) =>
		expect(personAudience({ person_id })).toBeNull()
	);

	it('converts a mirrored group UUID to the platform group ref', () => {
		expect(groupAudience({ id })).toEqual({ kind: 'group', id: `platform:group:${id}` });
		expect(groupAudience({ id: 'local-group' })).toBeNull();
		expect(groupAudience({ id: `platform:group:${id}` })).toBeNull();
	});

	it('requests bound people through the existing searchable directory', async () => {
		const fetcher = vi.fn(async () => new Response(JSON.stringify({ users: [], total: 0 })));
		vi.stubGlobal('fetch', fetcher);
		await searchUsers('session', 'Lark', 'name', 'asc', 2, undefined, true);
		const [url, init] = fetcher.mock.calls[0] as unknown as [string, RequestInit];
		const params = new URL(url, 'https://owui.invalid').searchParams;
		expect(params.get('platform_people')).toBe('true');
		expect(params.get('query')).toBe('Lark');
		expect(params.get('page')).toBe('2');
		expect(init.headers).toMatchObject({ Authorization: 'Bearer session' });
	});

	it.each([personAudience({ person_id: `platform:user:${id}` })!, groupAudience({ id })!])(
		'lists named shares and sends exact $kind references for share and removal',
		async (audience) => {
			const items = [{ audience, at: 7, name: 'Lark' }];
			const fetcher = vi.fn(async () => new Response(JSON.stringify({ items, next_cursor: null })));
			vi.stubGlobal('fetch', fetcher);
			const api = sharingAPI('session');
			expect((await api.list('chat/id')).items).toEqual(items);
			await api.share('chat/id', { audience, at: 9 });
			await api.unshare('chat/id', audience);
			const calls = fetcher.mock.calls as unknown as [string, RequestInit][];
			expect(calls[0][0]).toContain('/chats/chat%2Fid/share');
			expect(calls[0][1].method).toBe('GET');
			expect(JSON.parse(String(calls[1][1].body))).toEqual({ audience, at: 9 });
			expect(calls[2][1].method).toBe('DELETE');
			expect(JSON.parse(String(calls[2][1].body))).toEqual({ audience });
		}
	);
});
