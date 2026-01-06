import { describe, it, expect, vi, beforeEach } from 'vitest';
import { isFeatureEnabled, hasFeatureAccess, isAdminSettingsEnabled, isAdminSettingsTabEnabled, getFirstAvailableAdminSettingsTab, type Feature } from './features';
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

	it('returns true when config is not loaded yet', () => {
		vi.mocked(get).mockReturnValue(undefined);
		expect(isFeatureEnabled('voice')).toBe(true);
	});

	it('returns true when features object is undefined', () => {
		vi.mocked(get).mockReturnValue({});
		expect(isFeatureEnabled('voice')).toBe(true);
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

	it('allows admin bypass for permission path when feature is enabled', () => {
		vi.mocked(get).mockReturnValue({
			features: {
				feature_voice: true
			}
		});

		const adminUser = { role: 'admin' };
		expect(hasFeatureAccess('voice', adminUser, 'chat.tts')).toBe(true);
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

describe('admin_evaluations feature', () => {
	beforeEach(() => {
		vi.clearAllMocks();
	});

	it('returns true when admin_evaluations feature is enabled', () => {
		vi.mocked(get).mockReturnValue({
			features: {
				feature_admin_evaluations: true
			}
		});
		expect(isFeatureEnabled('admin_evaluations')).toBe(true);
	});

	it('returns false when admin_evaluations feature is disabled', () => {
		vi.mocked(get).mockReturnValue({
			features: {
				feature_admin_evaluations: false
			}
		});
		expect(isFeatureEnabled('admin_evaluations')).toBe(false);
	});

	it('returns true when admin_evaluations feature is undefined (default)', () => {
		vi.mocked(get).mockReturnValue({
			features: {}
		});
		expect(isFeatureEnabled('admin_evaluations')).toBe(true);
	});
});

describe('admin_functions feature', () => {
	beforeEach(() => {
		vi.clearAllMocks();
	});

	it('returns true when admin_functions feature is enabled', () => {
		vi.mocked(get).mockReturnValue({
			features: {
				feature_admin_functions: true
			}
		});
		expect(isFeatureEnabled('admin_functions')).toBe(true);
	});

	it('returns false when admin_functions feature is disabled', () => {
		vi.mocked(get).mockReturnValue({
			features: {
				feature_admin_functions: false
			}
		});
		expect(isFeatureEnabled('admin_functions')).toBe(false);
	});

	it('returns true when admin_functions feature is undefined (default)', () => {
		vi.mocked(get).mockReturnValue({
			features: {}
		});
		expect(isFeatureEnabled('admin_functions')).toBe(true);
	});
});

describe('admin_settings feature', () => {
	beforeEach(() => {
		vi.clearAllMocks();
	});

	it('returns true when admin_settings feature is enabled', () => {
		vi.mocked(get).mockReturnValue({
			features: {
				feature_admin_settings: true
			}
		});
		expect(isFeatureEnabled('admin_settings')).toBe(true);
	});

	it('returns false when admin_settings feature is disabled', () => {
		vi.mocked(get).mockReturnValue({
			features: {
				feature_admin_settings: false
			}
		});
		expect(isFeatureEnabled('admin_settings')).toBe(false);
	});

	it('returns true when admin_settings feature is undefined (default)', () => {
		vi.mocked(get).mockReturnValue({
			features: {}
		});
		expect(isFeatureEnabled('admin_settings')).toBe(true);
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

	it('admin_evaluations is a valid Feature type', () => {
		const feature: Feature = 'admin_evaluations';
		expect(feature).toBe('admin_evaluations');
	});

	it('admin_functions is a valid Feature type', () => {
		const feature: Feature = 'admin_functions';
		expect(feature).toBe('admin_functions');
	});

	it('admin_settings is a valid Feature type', () => {
		const feature: Feature = 'admin_settings';
		expect(feature).toBe('admin_settings');
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
			'tools',
			'admin_evaluations',
			'admin_functions',
			'admin_settings'
		];

		features.forEach((feature) => {
			expect(typeof feature).toBe('string');
		});
	});
});

describe('admin settings features', () => {
	describe('isAdminSettingsEnabled', () => {
		beforeEach(() => {
			vi.clearAllMocks();
		});

		it('returns true when feature_admin_settings is true', () => {
			vi.mocked(get).mockReturnValue({
				features: { feature_admin_settings: true }
			});
			expect(isAdminSettingsEnabled()).toBe(true);
		});

		it('returns false when feature_admin_settings is false', () => {
			vi.mocked(get).mockReturnValue({
				features: { feature_admin_settings: false }
			});
			expect(isAdminSettingsEnabled()).toBe(false);
		});

		it('returns true when feature_admin_settings is undefined (default)', () => {
			vi.mocked(get).mockReturnValue({
				features: {}
			});
			expect(isAdminSettingsEnabled()).toBe(true);
		});
	});

	describe('isAdminSettingsTabEnabled', () => {
		beforeEach(() => {
			vi.clearAllMocks();
		});

		it('returns true for any tab when tabs list is empty', () => {
			vi.mocked(get).mockReturnValue({
				features: {
					feature_admin_settings: true,
					feature_admin_settings_tabs: [],
					feature_voice: true
				}
			});
			expect(isAdminSettingsTabEnabled('general')).toBe(true);
			expect(isAdminSettingsTabEnabled('models')).toBe(true);
			expect(isAdminSettingsTabEnabled('pipelines')).toBe(true);
		});

		it('returns true only for tabs in the allowed list', () => {
			vi.mocked(get).mockReturnValue({
				features: {
					feature_admin_settings: true,
					feature_admin_settings_tabs: ['general', 'connections']
				}
			});
			expect(isAdminSettingsTabEnabled('general')).toBe(true);
			expect(isAdminSettingsTabEnabled('connections')).toBe(true);
			expect(isAdminSettingsTabEnabled('models')).toBe(false);
			expect(isAdminSettingsTabEnabled('pipelines')).toBe(false);
		});

		it('returns false for all tabs when admin settings is disabled', () => {
			vi.mocked(get).mockReturnValue({
				features: {
					feature_admin_settings: false,
					feature_admin_settings_tabs: []
				}
			});
			expect(isAdminSettingsTabEnabled('general')).toBe(false);
			expect(isAdminSettingsTabEnabled('models')).toBe(false);
		});

		it('audio tab requires both feature_voice and being in tabs list', () => {
			// Voice enabled, audio in list
			vi.mocked(get).mockReturnValue({
				features: {
					feature_admin_settings: true,
					feature_admin_settings_tabs: ['audio'],
					feature_voice: true
				}
			});
			expect(isAdminSettingsTabEnabled('audio')).toBe(true);

			// Voice disabled, audio in list
			vi.mocked(get).mockReturnValue({
				features: {
					feature_admin_settings: true,
					feature_admin_settings_tabs: ['audio'],
					feature_voice: false
				}
			});
			expect(isAdminSettingsTabEnabled('audio')).toBe(false);

			// Voice enabled, audio not in list
			vi.mocked(get).mockReturnValue({
				features: {
					feature_admin_settings: true,
					feature_admin_settings_tabs: ['general'],
					feature_voice: true
				}
			});
			expect(isAdminSettingsTabEnabled('audio')).toBe(false);

			// Voice enabled, empty list (all tabs allowed)
			vi.mocked(get).mockReturnValue({
				features: {
					feature_admin_settings: true,
					feature_admin_settings_tabs: [],
					feature_voice: true
				}
			});
			expect(isAdminSettingsTabEnabled('audio')).toBe(true);
		});
	});

	describe('getFirstAvailableAdminSettingsTab', () => {
		beforeEach(() => {
			vi.clearAllMocks();
		});

		it('returns general when all tabs enabled', () => {
			vi.mocked(get).mockReturnValue({
				features: {
					feature_admin_settings: true,
					feature_admin_settings_tabs: [],
					feature_voice: true
				}
			});
			expect(getFirstAvailableAdminSettingsTab()).toBe('general');
		});

		it('returns first tab from allowed list based on ADMIN_SETTINGS_TABS order', () => {
			vi.mocked(get).mockReturnValue({
				features: {
					feature_admin_settings: true,
					feature_admin_settings_tabs: ['models', 'connections'],
					feature_voice: true
				}
			});
			// Should return 'connections' because it comes before 'models' in ADMIN_SETTINGS_TABS order
			expect(getFirstAvailableAdminSettingsTab()).toBe('connections');
		});

		it('returns null when admin settings disabled', () => {
			vi.mocked(get).mockReturnValue({
				features: {
					feature_admin_settings: false
				}
			});
			expect(getFirstAvailableAdminSettingsTab()).toBe(null);
		});
	});
});
