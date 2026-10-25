// [Gradient] The data sources the caller can read (routers/data_sources.py).
import { WEBUI_API_BASE_URL } from '$lib/constants';

export type DataSource = { id: string; name: string; description: string };

export const getDataSources = async (token: string): Promise<DataSource[]> => {
	const res = await fetch(`${WEBUI_API_BASE_URL}/data-sources/`, {
		headers: { authorization: `Bearer ${token}` }
	});
	if (!res.ok) throw new Error(`Data sources unavailable: HTTP ${res.status}`);
	return res.json();
};
