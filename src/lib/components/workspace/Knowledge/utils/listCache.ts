export type KnowledgeListItem = {
	id: string;
	name: string;
	description?: string;
	updated_at: number;
	file_count?: number;
	write_access?: boolean;
	type?: string;
	suspension_info?: { days_remaining: number };
	meta?: {
		document?: unknown;
		source?: string;
		external?: { provider?: string; source?: { name?: string }; auth_mode?: string };
		[key: string]: unknown;
	};
	user?: {
		name?: string;
		email?: string;
	};
};

export type KnowledgeListPage = { items: KnowledgeListItem[]; total: number };

// Keep only first pages; returning copies prevents component edits from changing the snapshot.
export function createKnowledgeListCache(limit = 20) {
	const pages = new Map<string, KnowledgeListPage>();
	let revision = 0;
	return {
		get revision() {
			return revision;
		},
		get(key: string) {
			const page = pages.get(key);
			return page ? structuredClone(page) : undefined;
		},
		set(key: string, page: KnowledgeListPage, requestRevision: number) {
			if (requestRevision !== revision) return;
			pages.delete(key);
			pages.set(key, structuredClone(page));
			if (pages.size > limit) pages.delete(pages.keys().next().value!);
		},
		invalidate() {
			revision += 1;
			pages.clear();
		}
	};
}

export const knowledgeListCache = createKnowledgeListCache();
