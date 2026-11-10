import { get } from 'svelte/store';
import { config } from '$lib/stores';

export type Feature =
	// [Gradient] Match the backend tenant gates used by chat settings.
	| 'tool_servers'
	| 'terminal_servers'
	| 'user_demographics'
	| 'chat_controls'
	| 'capture'
	| 'artifacts'
	| 'document_writer'
	| 'playground'
	| 'chat_overview'
	| 'notes_ai_controls'
	| 'voice'
	| 'voice_call'
	| 'read_aloud'
	| 'changelog'
	| 'meetings'
	| 'system_prompt'
	| 'models'
	| 'knowledge'
	| 'prompts'
	| 'tools'
	| 'skills'
	| 'webpage_url'
	| 'reference_chats'
	| 'input_menu'
	| 'temporary_chat'
	| 'builtin_tools'
	| 'simple_assistant_builder';

/**
 * Check if a feature is enabled globally.
 * These flags apply to all users.
 * Used for SaaS tier-based feature control.
 */
export function isFeatureEnabled(feature: Feature): boolean {
	const $config = get(config);
	if (!$config?.features) {
		return false; // Config not loaded yet — hide features until authenticated config arrives
	}
	const key = `feature_${feature}` as keyof typeof $config.features;
	const value = $config.features[key];
	// If the key is explicitly set, use that value; otherwise default to true
	// (features are opt-out, so an absent key in the authenticated config means enabled)
	return typeof value === 'boolean' ? value : value == null ? true : Boolean(value);
}

/**
 * Check if user has access to a feature considering both:
 * 1. Global feature flag (tier-based, applies to everyone)
 * 2. User permission
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

	// Check the requested permission.
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
 * Whether chats are handled by the agent service: true whenever the agent
 * API is enabled, as on the backend (`main.py`, `route_to_agent`).
 *
 * On the agent route the agent service owns retrieval, so per-file
 * retrieval controls (e.g. the "Using Focused Retrieval" toggle) have
 * no effect and should be hidden.
 */
export function isAgentRouted(): boolean {
	return Boolean(get(config)?.features?.feature_agent_api_enabled);
}

/**
 * All valid chat control section IDs
 */
export const CHAT_CONTROL_SECTIONS = ['files', 'valves', 'system_prompt', 'params'] as const;

export type ChatControlSection = (typeof CHAT_CONTROL_SECTIONS)[number];

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

/**
 * Whether Continue Response is offered. The agent route sends one new input per
 * turn and cannot extend an earlier answer, so it never is there.
 */
export function canContinueResponse(
	$config: { features?: { feature_agent_api_enabled?: boolean } } | undefined,
	$user: { role?: string; permissions?: { chat?: { continue_response?: boolean } } } | undefined
): boolean {
	if ($config?.features?.feature_agent_api_enabled) return false;
	return $user?.permissions?.chat?.continue_response ?? true;
}

export function canRegenerateResponse(
	$config: { features?: { enable_v2_threads?: boolean } } | undefined,
	$user: { role?: string; permissions?: { chat?: { regenerate_response?: boolean } } } | undefined,
	isLastMessage: boolean,
	done: boolean,
	idle = true
): boolean {
	if (!($user?.permissions?.chat?.regenerate_response ?? true)) return false;
	return !$config?.features?.enable_v2_threads || (isLastMessage && done && idle);
}
