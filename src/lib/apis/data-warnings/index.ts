import { WEBUI_API_BASE_URL } from '$lib/constants';

export const logDataWarningAcceptance = async (
	token: string,
	chatId: string,
	modelId: string,
	capabilities: string[],
	warningMessage: string | null
) => {
	const res = await fetch(`${WEBUI_API_BASE_URL}/data-warnings/accept`, {
		method: 'POST',
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json',
			authorization: `Bearer ${token}`
		},
		body: JSON.stringify({
			chat_id: chatId,
			model_id: modelId,
			capabilities,
			warning_message: warningMessage
		})
	});

	if (!res.ok) {
		console.error('Failed to log data warning acceptance');
	}

	return res.json();
};
