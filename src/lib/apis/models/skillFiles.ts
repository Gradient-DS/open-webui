// [Gradient] Access to an assistant's folder always goes through model authorization.
import { WEBUI_API_BASE_URL } from '$lib/constants';

export type AssistantSkillFile = { path: string; file_id: string; size: number };

const request = async (token: string, id: string, suffix: string, init: RequestInit = {}) => {
	const response = await fetch(
		`${WEBUI_API_BASE_URL}/models/id/${encodeURIComponent(id)}/${suffix}`,
		{
			...init,
			headers: {
				Authorization: `Bearer ${token}`,
				'Content-Type': 'application/json',
				'Accept-Language': document.documentElement.lang,
				...init.headers
			}
		}
	);
	if (!response.ok) throw new Error((await response.json()).detail);
	return response;
};

export const getAssistantSkillFiles = async (
	token: string,
	id: string
): Promise<AssistantSkillFile[]> => (await request(token, id, 'skill-files')).json();

export const putAssistantSkillFile = async (
	token: string,
	id: string,
	path: string,
	fileId: string
) =>
	(
		await request(token, id, 'skill-files', {
			method: 'PUT',
			body: JSON.stringify({ path, file_id: fileId })
		})
	).json();

export const deleteAssistantSkillFile = async (token: string, id: string, path: string) =>
	(
		await request(token, id, `skill-files?${new URLSearchParams({ path })}`, { method: 'DELETE' })
	).json();

export const exportAssistantSkill = async (token: string, id: string): Promise<Blob> =>
	(await request(token, id, 'skill')).blob();
