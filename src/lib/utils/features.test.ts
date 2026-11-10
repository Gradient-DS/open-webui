import { describe, it, expect, vi, beforeEach } from 'vitest';
import {
	isFeatureEnabled,
	hasFeatureAccess,
	isAgentRouted,
	canContinueResponse,
	type Feature
} from './features';
import { get } from 'svelte/store';

// Mock svelte/store
vi.mock('svelte/store', () => ({
	get: vi.fn()
}));

// Mock the config store
vi.mock('$lib/stores', () => ({
	config: { subscribe: vi.fn() }
}));

describe('isFeatureEnabled', () => {
	beforeEach(() => {
		vi.clearAllMocks();
	});

	it.each([true, false])('honors the chat reference gate with v2 threads %s', (v2) => {
		vi.mocked(get).mockReturnValue({
			features: { enable_v2_threads: v2, feature_reference_chats: true }
		});
		expect(isFeatureEnabled('reference_chats')).toBe(true);
		vi.mocked(get).mockReturnValue({
			features: { enable_v2_threads: v2, feature_reference_chats: false }
		});
		expect(isFeatureEnabled('reference_chats')).toBe(false);
	});

	it('returns true when voice feature is enabled', () => {
		vi.mocked(get).mockReturnValue({
			features: {
				feature_voice: true
			}
		});
		expect(isFeatureEnabled('voice')).toBe(true);
	});

	it('returns false when voice feature is disabled', () => {
		vi.mocked(get).mockReturnValue({
			features: {
				feature_voice: false
			}
		});
		expect(isFeatureEnabled('voice')).toBe(false);
	});

	it('returns true when voice feature is undefined (default)', () => {
		vi.mocked(get).mockReturnValue({
			features: {}
		});
		expect(isFeatureEnabled('voice')).toBe(true);
	});

	it('returns false when config is not loaded yet', () => {
		vi.mocked(get).mockReturnValue(undefined);
		expect(isFeatureEnabled('voice')).toBe(false);
	});

	it('returns false when features object is undefined', () => {
		vi.mocked(get).mockReturnValue({});
		expect(isFeatureEnabled('voice')).toBe(false);
	});

	it('returns true when simple_assistant_builder is enabled', () => {
		vi.mocked(get).mockReturnValue({
			features: { feature_simple_assistant_builder: true }
		});
		expect(isFeatureEnabled('simple_assistant_builder')).toBe(true);
	});

	it('returns false when simple_assistant_builder is disabled', () => {
		vi.mocked(get).mockReturnValue({
			features: { feature_simple_assistant_builder: false }
		});
		expect(isFeatureEnabled('simple_assistant_builder')).toBe(false);
	});
});

describe('hasFeatureAccess', () => {
	beforeEach(() => {
		vi.clearAllMocks();
	});

	it('returns false when feature is globally disabled regardless of user role', () => {
		vi.mocked(get).mockReturnValue({
			features: {
				feature_voice: false
			}
		});

		const adminUser = { role: 'admin' };
		const regularUser = { role: 'user' };

		// Even admin cannot access disabled feature
		expect(hasFeatureAccess('voice', adminUser)).toBe(false);
		expect(hasFeatureAccess('voice', regularUser)).toBe(false);
	});

	it('returns true when feature is enabled and no permission path specified', () => {
		vi.mocked(get).mockReturnValue({
			features: {
				feature_voice: true
			}
		});

		const regularUser = { role: 'user' };
		expect(hasFeatureAccess('voice', regularUser)).toBe(true);
	});

	it('enforces permissions for a stored legacy role', () => {
		vi.mocked(get).mockReturnValue({
			features: {
				feature_voice: true
			}
		});

		const legacyUser = { role: 'admin', permissions: { chat: { tts: false } } };
		expect(hasFeatureAccess('voice', legacyUser, 'chat.tts')).toBe(false);
	});

	it('checks user permission when feature is enabled and user is not admin', () => {
		vi.mocked(get).mockReturnValue({
			features: {
				feature_voice: true
			}
		});

		const userWithPermission = {
			role: 'user',
			permissions: { chat: { tts: true } }
		};

		const userWithoutPermission = {
			role: 'user',
			permissions: { chat: { tts: false } }
		};

		expect(hasFeatureAccess('voice', userWithPermission, 'chat.tts')).toBe(true);
		expect(hasFeatureAccess('voice', userWithoutPermission, 'chat.tts')).toBe(false);
	});
});

describe('changelog feature', () => {
	beforeEach(() => {
		vi.clearAllMocks();
	});

	it('returns true when changelog feature is enabled', () => {
		vi.mocked(get).mockReturnValue({
			features: {
				feature_changelog: true
			}
		});
		expect(isFeatureEnabled('changelog')).toBe(true);
	});

	it('returns false when changelog feature is disabled', () => {
		vi.mocked(get).mockReturnValue({
			features: {
				feature_changelog: false
			}
		});
		expect(isFeatureEnabled('changelog')).toBe(false);
	});

	it('returns true when changelog feature is undefined (default)', () => {
		vi.mocked(get).mockReturnValue({
			features: {}
		});
		expect(isFeatureEnabled('changelog')).toBe(true);
	});
});

describe('system_prompt feature', () => {
	beforeEach(() => {
		vi.clearAllMocks();
	});

	it('returns true when system_prompt feature is enabled', () => {
		vi.mocked(get).mockReturnValue({
			features: {
				feature_system_prompt: true
			}
		});
		expect(isFeatureEnabled('system_prompt')).toBe(true);
	});

	it('returns false when system_prompt feature is disabled', () => {
		vi.mocked(get).mockReturnValue({
			features: {
				feature_system_prompt: false
			}
		});
		expect(isFeatureEnabled('system_prompt')).toBe(false);
	});

	it('returns true when system_prompt feature is undefined (default)', () => {
		vi.mocked(get).mockReturnValue({
			features: {}
		});
		expect(isFeatureEnabled('system_prompt')).toBe(true);
	});
});

describe('Feature types', () => {
	it('voice is a valid Feature type', () => {
		// This test ensures TypeScript compilation passes with 'voice' as a Feature
		const feature: Feature = 'voice';
		expect(feature).toBe('voice');
	});

	it('changelog is a valid Feature type', () => {
		// This test ensures TypeScript compilation passes with 'changelog' as a Feature
		const feature: Feature = 'changelog';
		expect(feature).toBe('changelog');
	});

	it('system_prompt is a valid Feature type', () => {
		// This test ensures TypeScript compilation passes with 'system_prompt' as a Feature
		const feature: Feature = 'system_prompt';
		expect(feature).toBe('system_prompt');
	});

	it('all expected features are valid Feature types', () => {
		const features: Feature[] = [
			'chat_controls',
			'capture',
			'artifacts',
			'playground',
			'chat_overview',
			'notes_ai_controls',
			'voice',
			'changelog',
			'system_prompt',
			'models',
			'knowledge',
			'prompts',
			'tools'
		];

		features.forEach((feature) => {
			expect(typeof feature).toBe('string');
		});
	});
});

describe('isAgentRouted', () => {
	it.each([
		[true, true],
		[false, false],
		[undefined, false]
	])('follows the agent API flag (%s)', (api, routed) => {
		vi.mocked(get).mockReturnValue({ features: { feature_agent_api_enabled: api } });
		expect(isAgentRouted()).toBe(routed);
	});
});

describe('canContinueResponse', () => {
	it.each([
		[{ role: 'user' }, true],
		[{ role: 'admin', permissions: { chat: { continue_response: false } } }, false],
		[{ role: 'user', permissions: { chat: { continue_response: false } } }, false]
	])('follows the permission off the agent route (%o)', (user, allowed) => {
		expect(canContinueResponse({ features: {} }, user)).toBe(allowed);
	});

	it('is never offered on the agent route', () => {
		const agent = { features: { feature_agent_api_enabled: true } };
		expect(canContinueResponse(agent, { role: 'admin' })).toBe(false);
	});
});
