// Caller-scoped provisioned knowledge.
import { WEBUI_API_BASE_URL } from '$lib/constants';

export type ProvisionedKnowledge = { id: string; name: string; description: string };

export const getProvisionedKnowledge = async (token: string): Promise<ProvisionedKnowledge[]> => {
	const res = await fetch(`${WEBUI_API_BASE_URL}/provisioned-knowledge/`, {
		headers: { authorization: `Bearer ${token}` }
	});
	if (!res.ok) throw new Error(`Data sources unavailable: HTTP ${res.status}`);
	return res.json();
};
