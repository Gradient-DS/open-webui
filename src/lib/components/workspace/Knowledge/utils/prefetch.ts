import { getKnowledgeById, searchKnowledgeFilesById } from '$lib/apis/knowledge';

const TTL = 30_000;
type Entry = {
	promise: Promise<unknown>;
	expires: number;
	timer: ReturnType<typeof setTimeout>;
	valid: boolean;
};

export function createKnowledgePrefetchCache() {
	const entries = new Map<string, Entry>();
	const drop = (key: string) => {
		const entry = entries.get(key);
		if (entry) {
			clearTimeout(entry.timer);
			entries.delete(key);
		}
	};
	return {
		prefetch<T>(key: string, load: () => Promise<T>) {
			const previous = entries.get(key);
			if (previous && previous.expires > Date.now()) return;
			drop(key);
			const entry: Entry = {
				promise: load().catch(() => undefined),
				expires: Date.now() + TTL,
				timer: setTimeout(() => drop(key), TTL),
				valid: true
			};
			entries.set(key, entry);
		},
		take<T>(key: string, load: () => Promise<T>) {
			const entry = entries.get(key);
			drop(key);
			const request = {
				reused: false,
				promise:
					entry && entry.expires > Date.now()
						? entry.promise.then((value) => {
								if (value == null || !entry.valid) return load();
								request.reused = true;
								return value as T;
							})
						: load()
			};
			return request;
		},
		invalidate() {
			for (const [key, entry] of entries) {
				entry.valid = false;
				drop(key);
			}
		}
	};
}

// Preserve undefined vs null: for directory_id these mean all levels vs root.
export const knowledgeRequestKey = (userId: string | undefined, kind: string, args: unknown[]) =>
	JSON.stringify([
		userId,
		kind,
		args.map((value) => (value === undefined ? { omitted: true } : value))
	]);

const cache = createKnowledgePrefetchCache();
export const invalidateKnowledgePrefetch = () => cache.invalidate();

export const initialKnowledgeItemsArgs = (
	token: string,
	id: string
): Parameters<typeof searchKnowledgeFilesById> => [
	token,
	id,
	'',
	null,
	null,
	null,
	1,
	null,
	true,
	null
];

export function prefetchKnowledge(userId: string | undefined, token: string, id: string) {
	cache.prefetch(knowledgeRequestKey(userId, 'detail', [token, id]), () =>
		getKnowledgeById(token, id)
	);
	const args = initialKnowledgeItemsArgs(token, id);
	cache.prefetch(knowledgeRequestKey(userId, 'items', args), () =>
		searchKnowledgeFilesById(...args)
	);
}

export const takeKnowledgeDetail = (userId: string | undefined, token: string, id: string) =>
	cache.take(knowledgeRequestKey(userId, 'detail', [token, id]), () => getKnowledgeById(token, id));

export const takeKnowledgeItems = (
	userId: string | undefined,
	args: Parameters<typeof searchKnowledgeFilesById>
) =>
	cache.take(knowledgeRequestKey(userId, 'items', args), () => searchKnowledgeFilesById(...args));
