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
	| 'system_prompt'
	| 'models'
	| 'knowledge'
	| 'prompts'
	| 'tools'
	| 'admin_evaluations'
	| 'admin_functions'
	| 'admin_settings';

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

/**
 * All valid admin settings tab IDs
 */
export const ADMIN_SETTINGS_TABS = [
	'general',
	'connections',
	'models',
	'evaluations',
	'tools',
	'documents',
	'web',
	'code-execution',
	'interface',
	'audio',
	'images',
	'pipelines',
	'db',
	'email'
] as const;

export type AdminSettingsTab = (typeof ADMIN_SETTINGS_TABS)[number];

/**
 * All valid chat control section IDs
 */
export const CHAT_CONTROL_SECTIONS = ['files', 'valves', 'system_prompt', 'params'] as const;

export type ChatControlSection = (typeof CHAT_CONTROL_SECTIONS)[number];

/**
 * Check if admin settings section is enabled globally.
 */
export function isAdminSettingsEnabled(): boolean {
	const $config = get(config);
	return $config?.features?.feature_admin_settings ?? true;
}

/**
 * Check if a specific admin settings tab is enabled.
 * For the audio tab, also requires FEATURE_VOICE to be true.
 *
 * @param tab - The tab ID to check
 * @returns true if the tab should be visible
 */
export function isAdminSettingsTabEnabled(tab: AdminSettingsTab): boolean {
	// First check if admin settings is enabled at all
	if (!isAdminSettingsEnabled()) {
		return false;
	}

	const $config = get(config);
	const allowedTabs = $config?.features?.feature_admin_settings_tabs ?? [];

	// If no tabs specified, all tabs are allowed
	const tabAllowed = allowedTabs.length === 0 || allowedTabs.includes(tab);

	// Audio tab has additional requirement: FEATURE_VOICE must be true
	if (tab === 'audio') {
		return tabAllowed && isFeatureEnabled('voice');
	}

	return tabAllowed;
}

/**
 * Get the first available admin settings tab.
 * Used for redirecting when accessing a disabled tab.
 *
 * @returns The first enabled tab ID, or null if none available
 */
export function getFirstAvailableAdminSettingsTab(): AdminSettingsTab | null {
	for (const tab of ADMIN_SETTINGS_TABS) {
		if (isAdminSettingsTabEnabled(tab)) {
			return tab;
		}
	}
	return null;
}

/**
 * Check if a specific chat control section is enabled.
 * Requires FEATURE_CHAT_CONTROLS to be true first.
 *
 * @param section - The section ID to check
 * @returns true if the section should be visible
 */
export function isChatControlSectionEnabled(section: ChatControlSection): boolean {
	// First check if chat controls is enabled at all
	if (!isFeatureEnabled('chat_controls')) {
		return false;
	}

	const $config = get(config);
	const allowedSections = $config?.features?.feature_chat_controls_sections ?? [];

	// If no sections specified, all sections are allowed
	return allowedSections.length === 0 || allowedSections.includes(section);
}
