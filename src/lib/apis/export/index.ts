import { WEBUI_API_BASE_URL } from '$lib/constants';

export const triggerDataExport = async (token: string) => {
	const res = await fetch(`${WEBUI_API_BASE_URL}/export/data`, {
		method: 'POST',
		headers: {
			'Content-Type': 'application/json',
			Authorization: `Bearer ${token}`
		}
	});

	if (!res.ok) throw await res.json();
	return res.json();
};

export const getExportStatus = async (token: string) => {
	const res = await fetch(`${WEBUI_API_BASE_URL}/export/data/status`, {
		method: 'GET',
		headers: {
			Authorization: `Bearer ${token}`
		}
	});

	if (!res.ok) throw await res.json();
	return res.json();
};

export const deleteExport = async (token: string) => {
	const res = await fetch(`${WEBUI_API_BASE_URL}/export/data`, {
		method: 'DELETE',
		headers: {
			Authorization: `Bearer ${token}`
		}
	});

	if (!res.ok) throw await res.json();
	return res.json();
};
