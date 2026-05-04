import { WEBUI_API_BASE_URL } from '$lib/constants';

export interface AgentResponse {
	id?: string;
	description?: string | null;
	config?: {
		welcome_message?: string | null;
		[key: string]: unknown;
	} | null;
	[key: string]: unknown;
}

let cached: Promise<AgentResponse | null> | null = null;

const fetchDefaultAgent = async (token: string): Promise<AgentResponse | null> => {
	const res = await fetch(`${WEBUI_API_BASE_URL}/agents`, {
		method: 'GET',
		headers: {
			Accept: 'application/json',
			authorization: `Bearer ${token}`
		}
	});
	if (res.status === 404) return null;
	if (!res.ok) throw new Error(`getDefaultAgent: ${res.status}`);
	return res.json();
};

export const getDefaultAgent = (token: string): Promise<AgentResponse | null> => {
	if (cached) return cached;
	cached = fetchDefaultAgent(token).catch((err) => {
		cached = null;
		throw err;
	});
	return cached;
};
