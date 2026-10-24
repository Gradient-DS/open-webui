/**
 * Shared helpers for managing system-level connections.
 * Used by both the admin settings UI and the desktop event handler
 * to ensure consistent add/remove logic.
 */

import { getTerminalServerConnections, setTerminalServerConnections } from '$lib/apis/configs';

// ─── Terminal Server Connections ────────────────────────

/**
 * Add a terminal server connection at the system level.
 * Mirrors the logic in admin/Settings/Integrations.svelte.
 */
export const addTerminalConnection = async (
	token: string,
	connection: { url: string; key?: string; name?: string; auth_type?: string }
) => {
	const current = await getTerminalServerConnections(token);
	const servers = current?.TERMINAL_SERVER_CONNECTIONS ?? [];

	// Don't add duplicates
	if (servers.find((s: any) => s.url === connection.url)) {
		return current;
	}

	servers.push({
		url: connection.url,
		key: connection.key ?? '',
		auth_type: connection.auth_type ?? 'bearer',
		name: connection.name ?? 'Open Terminal',
		enabled: true
	});

	return await setTerminalServerConnections(token, {
		TERMINAL_SERVER_CONNECTIONS: servers
	});
};

/**
 * Remove a terminal server connection by URL at the system level.
 */
export const removeTerminalConnection = async (token: string, url: string) => {
	const current = await getTerminalServerConnections(token);
	const servers = current?.TERMINAL_SERVER_CONNECTIONS ?? [];

	const filtered = servers.filter((s: any) => s.url !== url);
	if (filtered.length === servers.length) return current; // nothing to remove

	return await setTerminalServerConnections(token, {
		TERMINAL_SERVER_CONNECTIONS: filtered
	});
};
