import i18next from 'i18next';
import { AUDIO_API_BASE_URL } from '$lib/constants';

export const transcribeAudio = async (token: string, file: File, language?: string) => {
	const data = new FormData();
	data.append('file', file);
	if (language) {
		data.append('language', language);
	}

	let error = null;
	const res = await fetch(`${AUDIO_API_BASE_URL}/transcriptions`, {
		method: 'POST',
		headers: {
			Accept: 'application/json',
			'Accept-Language': i18next.resolvedLanguage ?? i18next.language ?? 'en-US',
			authorization: `Bearer ${token}`
		},
		body: data
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			error = err.detail?.detail ?? err.detail;
			console.error(err);
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const synthesizeSpeech = async (
	token: string = '',
	speaker: string = '',
	text: string = ''
) => {
	let error = null;

	const res = await fetch(`${AUDIO_API_BASE_URL}/speech`, {
		method: 'POST',
		headers: {
			Authorization: `Bearer ${token}`,
			'Content-Type': 'application/json',
			'Accept-Language': i18next.resolvedLanguage ?? i18next.language ?? 'en-US'
		},
		body: JSON.stringify({
			input: text,
			...(speaker && { voice: speaker }),
			response_format: 'mp3'
		})
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res;
		})
		.catch((err) => {
			error = err.detail?.detail ?? err.detail;
			console.error(err);

			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};
