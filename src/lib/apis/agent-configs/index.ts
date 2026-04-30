import { WEBUI_API_BASE_URL } from '$lib/constants';

export interface AgentAccessGrant {
	id?: string;
	principal_type: 'user' | 'group';
	principal_id: string;
	permission: 'read' | 'write';
}

export interface AgentConfigModel {
	id: string;
	user_id?: string | null;
	name: string;
	description?: string | null;
	profile_image_url?: string | null;
	cta_copy?: string | null;
	is_active: boolean;
	is_beta: boolean;
	meta?: Record<string, unknown>;
	position: number;
	access_grants: AgentAccessGrant[];
	created_at: number;
	updated_at: number;
}

export interface AgentConfigUserResponse {
	id: string;
	name: string;
	description?: string | null;
	profile_image_url?: string | null;
	cta_copy?: string | null;
	is_beta: boolean;
}

export interface AgentConfigDetectionRow {
	slug: string;
	in_env: boolean;
	configured: boolean;
	config?: AgentConfigModel | null;
}

export interface AgentConfigForm {
	name: string;
	description?: string | null;
	profile_image_url?: string | null;
	cta_copy?: string | null;
	is_active: boolean;
	is_beta: boolean;
	meta?: Record<string, unknown>;
	access_grants?: AgentAccessGrant[];
}

const headers = (token: string) => ({
	Accept: 'application/json',
	'Content-Type': 'application/json',
	authorization: `Bearer ${token}`
});

async function request<T>(url: string, init?: RequestInit): Promise<T> {
	const res = await fetch(url, init);
	if (!res.ok) {
		const err = await res.json().catch(() => ({ detail: res.statusText }));
		throw err;
	}
	return res.json();
}

export const detectAgents = async (token: string): Promise<AgentConfigDetectionRow[]> =>
	request(`${WEBUI_API_BASE_URL}/agent-configs/detect`, {
		method: 'GET',
		headers: headers(token)
	});

export const listVisibleAgents = async (token: string): Promise<AgentConfigUserResponse[]> =>
	request(`${WEBUI_API_BASE_URL}/agent-configs/`, {
		method: 'GET',
		headers: headers(token)
	});

export const getAgentConfig = async (token: string, slug: string): Promise<AgentConfigModel> =>
	request(`${WEBUI_API_BASE_URL}/agent-configs/${encodeURIComponent(slug)}`, {
		method: 'GET',
		headers: headers(token)
	});

export const createAgentConfig = async (
	token: string,
	slug: string,
	form: AgentConfigForm
): Promise<AgentConfigModel> =>
	request(`${WEBUI_API_BASE_URL}/agent-configs/${encodeURIComponent(slug)}`, {
		method: 'POST',
		headers: headers(token),
		body: JSON.stringify(form)
	});

export const updateAgentConfig = async (
	token: string,
	slug: string,
	form: AgentConfigForm
): Promise<AgentConfigModel> =>
	request(`${WEBUI_API_BASE_URL}/agent-configs/${encodeURIComponent(slug)}/update`, {
		method: 'POST',
		headers: headers(token),
		body: JSON.stringify(form)
	});

export const deleteAgentConfig = async (token: string, slug: string): Promise<{ ok: boolean }> =>
	request(`${WEBUI_API_BASE_URL}/agent-configs/${encodeURIComponent(slug)}`, {
		method: 'DELETE',
		headers: headers(token)
	});

export const reorderAgentConfigs = async (
	token: string,
	slugs: string[]
): Promise<AgentConfigModel[]> =>
	request(`${WEBUI_API_BASE_URL}/agent-configs/reorder`, {
		method: 'POST',
		headers: headers(token),
		body: JSON.stringify({ slugs })
	});
