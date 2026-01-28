import { WEBUI_API_BASE_URL } from '$lib/constants';

export const getArchives = async (
	token: string,
	params: { search?: string; include_restored?: boolean; skip?: number; limit?: number } = {}
) => {
	let error = null;

	const searchParams = new URLSearchParams();
	if (params.search) searchParams.set('search', params.search);
	if (params.include_restored) searchParams.set('include_restored', 'true');
	if (params.skip !== undefined) searchParams.set('skip', params.skip.toString());
	if (params.limit !== undefined) searchParams.set('limit', params.limit.toString());
	// Cache busting to ensure fresh data
	searchParams.set('_t', Date.now().toString());

	const res = await fetch(`${WEBUI_API_BASE_URL}/archives/?${searchParams}`, {
		method: 'GET',
		headers: {
			'Content-Type': 'application/json',
			Authorization: `Bearer ${token}`,
			'Cache-Control': 'no-cache'
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

export const getArchive = async (token: string, archiveId: string) => {
	let error = null;

	const res = await fetch(`${WEBUI_API_BASE_URL}/archives/${archiveId}`, {
		method: 'GET',
		headers: {
			'Content-Type': 'application/json',
			Authorization: `Bearer ${token}`
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

export const createArchive = async (
	token: string,
	userId: string,
	data: { reason: string; retention_days?: number; never_delete?: boolean }
) => {
	let error = null;

	const res = await fetch(`${WEBUI_API_BASE_URL}/archives/user/${userId}`, {
		method: 'POST',
		headers: {
			'Content-Type': 'application/json',
			Authorization: `Bearer ${token}`
		},
		body: JSON.stringify(data)
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

export const exportArchiveChats = async (token: string, archiveId: string) => {
	let error = null;

	const res = await fetch(`${WEBUI_API_BASE_URL}/archives/${archiveId}/export`, {
		method: 'GET',
		headers: {
			'Content-Type': 'application/json',
			Authorization: `Bearer ${token}`
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

export const updateArchive = async (
	token: string,
	archiveId: string,
	data: { reason?: string; retention_days?: number; never_delete?: boolean }
) => {
	let error = null;

	const res = await fetch(`${WEBUI_API_BASE_URL}/archives/${archiveId}`, {
		method: 'PATCH',
		headers: {
			'Content-Type': 'application/json',
			Authorization: `Bearer ${token}`
		},
		body: JSON.stringify(data)
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

export const deleteArchive = async (token: string, archiveId: string) => {
	let error = null;

	const res = await fetch(`${WEBUI_API_BASE_URL}/archives/${archiveId}`, {
		method: 'DELETE',
		headers: {
			'Content-Type': 'application/json',
			Authorization: `Bearer ${token}`
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

export const getArchiveConfig = async (token: string) => {
	let error = null;

	const res = await fetch(`${WEBUI_API_BASE_URL}/archives/admin/config`, {
		method: 'GET',
		headers: {
			'Content-Type': 'application/json',
			Authorization: `Bearer ${token}`
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

export const updateArchiveConfig = async (
	token: string,
	config: {
		enable_user_archival?: boolean;
		default_archive_retention_days?: number;
		enable_auto_archive_on_self_delete?: boolean;
		auto_archive_retention_days?: number;
	}
) => {
	let error = null;

	const res = await fetch(`${WEBUI_API_BASE_URL}/archives/admin/config`, {
		method: 'POST',
		headers: {
			'Content-Type': 'application/json',
			Authorization: `Bearer ${token}`
		},
		body: JSON.stringify(config)
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
