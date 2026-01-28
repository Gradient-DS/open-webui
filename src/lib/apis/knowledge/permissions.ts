/**
 * Knowledge Permissions API
 *
 * Handles permission validation for knowledge base sharing.
 * Separate module to avoid modifying upstream knowledge/index.ts.
 */

import { WEBUI_API_BASE_URL } from '$lib/constants';

export interface SharingRecommendation {
	user_id: string;
	user_name: string;
	user_email: string;
	source_type: string;
	inaccessible_count: number;
	grant_access_url: string | null;
}

export interface ShareValidationResult {
	can_share: boolean;
	can_share_to_users: string[];
	cannot_share_to_users: string[];
	blocking_resources: Record<string, string[]>;
	recommendations: SharingRecommendation[];
	source_restricted: boolean;
}

export interface UserAccessStatus {
	user_id: string;
	user_name: string;
	user_email: string;
	has_source_access: boolean;
	has_kb_access: boolean;
	missing_resources: string[];
	missing_resource_count: number;
	source_type: string;
	grant_access_url: string | null;
}

export interface FileAdditionConflict {
	has_conflict: boolean;
	kb_is_public: boolean;
	users_without_access: string[];
	user_details: SharingRecommendation[];
	source_type: string;
	grant_access_url: string | null;
}

export const validateKnowledgeShare = async (
	token: string,
	knowledgeId: string,
	userIds: string[],
	groupIds: string[]
): Promise<ShareValidationResult | null> => {
	let error = null;

	const res = await fetch(`${WEBUI_API_BASE_URL}/knowledge/${knowledgeId}/validate-share`, {
		method: 'POST',
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json',
			authorization: `Bearer ${token}`
		},
		body: JSON.stringify({
			user_ids: userIds,
			group_ids: groupIds
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

export const getUsersReadyForAccess = async (
	token: string,
	knowledgeId: string
): Promise<UserAccessStatus[]> => {
	let error = null;

	const res = await fetch(`${WEBUI_API_BASE_URL}/knowledge/${knowledgeId}/users-ready-for-access`, {
		method: 'GET',
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json',
			authorization: `Bearer ${token}`
		}
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			console.error(err);
			error = err.detail;
			return { users: [] };
		});

	if (error) {
		throw error;
	}

	return res.users;
};

export const validateFileAddition = async (
	token: string,
	knowledgeId: string,
	fileIds: string[]
): Promise<FileAdditionConflict | null> => {
	let error = null;

	const res = await fetch(
		`${WEBUI_API_BASE_URL}/knowledge/${knowledgeId}/validate-file-addition`,
		{
			method: 'POST',
			headers: {
				Accept: 'application/json',
				'Content-Type': 'application/json',
				authorization: `Bearer ${token}`
			},
			body: JSON.stringify({
				file_ids: fileIds
			})
		}
	)
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
