// [Gradient] A single discovery contract for current APIs and future connection kinds.
import { WEBUI_API_BASE_URL } from '$lib/constants';

export type ToolSource = {
	id: string;
	kind: string;
	name: string;
	group_name: string;
	countries: string[];
};

export async function getToolSources(token: string, agent?: string): Promise<ToolSource[]> {
	const query = agent === undefined ? '' : `?${new URLSearchParams({ agent })}`;
	const response = await fetch(`${WEBUI_API_BASE_URL}/tool-sources${query}`, {
		headers: { Accept: 'application/json', authorization: `Bearer ${token}` }
	});
	if (!response.ok) throw new Error(`HTTP ${response.status}`);
	return (await response.json()).data;
}

export function availableToolSourceIds(ids: string[], sources: ToolSource[]): string[] {
	const available = new Set(sources.map(({ id }) => id));
	return [...new Set(ids)].filter((id) => available.has(id)).slice(0, 8);
}
