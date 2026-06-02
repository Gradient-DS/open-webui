import { WEBUI_API_BASE_URL } from '$lib/constants';

export const submitFeedbackReport = async (token: string, payload: object) => {
	let error = null;

	const res = await fetch(`${WEBUI_API_BASE_URL}/feedback/report`, {
		method: 'POST',
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json',
			authorization: `Bearer ${token}`
		},
		body: JSON.stringify(payload)
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			error = err.detail ?? err;
			console.error(err);
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};
