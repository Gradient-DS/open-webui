import { WEBUI_API_BASE_URL } from '$lib/constants';

export const createInvite = async (
	token: string,
	name: string,
	email: string,
	role: string,
	sendEmail: boolean
) => {
	let error = null;

	const res = await fetch(`${WEBUI_API_BASE_URL}/invites/`, {
		method: 'POST',
		headers: {
			'Content-Type': 'application/json',
			...(token && { authorization: `Bearer ${token}` })
		},
		body: JSON.stringify({
			name,
			email,
			role,
			send_email: sendEmail
		})
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			console.error(err);
			error = err.detail;
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const validateInvite = async (inviteToken: string) => {
	let error = null;

	const res = await fetch(`${WEBUI_API_BASE_URL}/invites/${inviteToken}/validate`, {
		method: 'GET',
		headers: {
			'Content-Type': 'application/json'
		}
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			console.error(err);
			error = err.detail;
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const acceptInvite = async (
	inviteToken: string,
	password: string,
	name?: string
) => {
	let error = null;

	const res = await fetch(`${WEBUI_API_BASE_URL}/invites/${inviteToken}/accept`, {
		method: 'POST',
		headers: {
			'Content-Type': 'application/json'
		},
		body: JSON.stringify({
			password,
			...(name && { name })
		})
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			console.error(err);
			error = err.detail;
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const listInvites = async (token: string) => {
	let error = null;

	const res = await fetch(`${WEBUI_API_BASE_URL}/invites/`, {
		method: 'GET',
		headers: {
			'Content-Type': 'application/json',
			...(token && { authorization: `Bearer ${token}` })
		}
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			console.error(err);
			error = err.detail;
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const resendInvite = async (token: string, inviteId: string) => {
	let error = null;

	const res = await fetch(`${WEBUI_API_BASE_URL}/invites/${inviteId}/resend`, {
		method: 'POST',
		headers: {
			'Content-Type': 'application/json',
			...(token && { authorization: `Bearer ${token}` })
		}
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			console.error(err);
			error = err.detail;
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const revokeInvite = async (token: string, inviteId: string) => {
	let error = null;

	const res = await fetch(`${WEBUI_API_BASE_URL}/invites/${inviteId}`, {
		method: 'DELETE',
		headers: {
			'Content-Type': 'application/json',
			...(token && { authorization: `Bearer ${token}` })
		}
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			console.error(err);
			error = err.detail;
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};
