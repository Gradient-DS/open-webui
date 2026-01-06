import { get } from 'svelte/store';
import { config } from '$lib/stores';

export type Feature =
	| 'chat_controls'
	| 'capture'
	| 'artifacts'
	| 'playground'
	| 'chat_overview'
	| 'notes_ai_controls'
	| 'voice'
	| 'changelog'
	| 'system_prompt';

/**
 * Check if a feature is enabled globally.
 * These flags apply to ALL users including admins.
 * Used for SaaS tier-based feature control.
 */
export function isFeatureEnabled(feature: Feature): boolean {
	const $config = get(config);
	if (!$config?.features) {
		return true; // Default to true if config not loaded yet
	}
	const key = `feature_${feature}` as keyof typeof $config.features;
	// Default to true if not set (backwards compatibility)
	return $config.features[key] ?? true;
}

/**
 * Check if user has access to a feature considering both:
 * 1. Global feature flag (tier-based, applies to everyone)
 * 2. User permission (role-based, admins bypass)
 *
 * @param feature - The feature to check
 * @param user - The user object with role and permissions
 * @param permissionPath - Optional dot-notation path to permission (e.g., 'chat.controls')
 */
interface PermissionTree {
	[key: string]: boolean | PermissionTree;
}

export function hasFeatureAccess(
	feature: Feature,
	user: { role?: string; permissions?: PermissionTree } | null,
	permissionPath?: string
): boolean {
	// First check: feature must be enabled globally
	if (!isFeatureEnabled(feature)) {
		return false;
	}

	// If no permission check needed, feature is accessible
	if (!permissionPath) {
		return true;
	}

	// Admin bypass for permissions (but NOT for feature flags)
	if (user?.role === 'admin') {
		return true;
	}

	// Check specific permission for non-admins
	const keys = permissionPath.split('.');
	let perms: boolean | PermissionTree | undefined = user?.permissions;
	for (const key of keys) {
		if (typeof perms === 'object' && perms !== null) {
			perms = perms[key];
		} else {
			perms = undefined;
			break;
		}
	}
	return Boolean(perms ?? true); // Default to true if permission not set
}
