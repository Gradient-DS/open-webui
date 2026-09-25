import { writable } from 'svelte/store';

export type WorkspaceSection = 'models' | 'knowledge' | 'prompts' | 'skills' | 'tools';
type CountResponse = { total?: number } | unknown[] | null;
type CountLoaders = Record<WorkspaceSection, () => Promise<CountResponse>>;

export const workspaceCounts = writable<Record<WorkspaceSection, number | null>>({
	models: null,
	knowledge: null,
	prompts: null,
	skills: null,
	tools: null
});

const revisions = new Map<WorkspaceSection, number>();
let loaders: CountLoaders | undefined;

export function setWorkspaceCount(section: WorkspaceSection, total: number | null) {
	revisions.set(section, (revisions.get(section) ?? 0) + 1);
	workspaceCounts.update((counts) => ({ ...counts, [section]: total }));
}

async function refreshWorkspaceCount(section: WorkspaceSection) {
	const activeLoaders = loaders;
	if (!activeLoaders) return;
	const revision = (revisions.get(section) ?? 0) + 1;
	revisions.set(section, revision);
	try {
		const result = await activeLoaders[section]();
		// A page total or later mutation takes precedence over this request.
		if (loaders === activeLoaders && revisions.get(section) === revision) {
			setWorkspaceCount(section, Array.isArray(result) ? result.length : (result?.total ?? null));
		}
	} catch {
		// Count failures must not affect a successful mutation or page navigation.
	}
}

export function notifyWorkspaceMutation(section: WorkspaceSection) {
	void refreshWorkspaceCount(section);
}

export function registerWorkspaceCountLoaders(next: CountLoaders) {
	loaders = next;
	for (const section of Object.keys(next) as WorkspaceSection[]) {
		void refreshWorkspaceCount(section);
	}
	return () => {
		if (loaders === next) loaders = undefined;
	};
}
