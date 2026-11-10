import { afterEach, describe, expect, it } from 'vitest';
import { render } from 'svelte/server';
import { writable } from 'svelte/store';
import { config } from '$lib/stores';
import { authOnboarding } from '$lib/utils/authPage';
import OnBoarding from './OnBoarding.svelte';

afterEach(() => config.set(undefined));

describe('first-admin onboarding', () => {
	it.each([false, true])('never renders in platform mode with onboarding=%s', (onboarding) => {
		const backendConfig = { onboarding, oauth: { required_provider: 'oidc' } };
		config.set(backendConfig as any);
		expect(authOnboarding(backendConfig)).toBe(false);
		const html = render(OnBoarding, {
			props: { show: true },
			context: new Map([['i18n', writable({ t: (key: string) => key })]])
		}).body;
		expect(html).not.toContain('Welcome to your AI home.');
		expect(html).not.toContain('<video');
		expect(html).not.toContain('<button');
	});

	it('preserves onboarding for an empty standalone installation', () => {
		const backendConfig = { onboarding: true, oauth: { required_provider: null } };
		config.set(backendConfig as any);
		expect(authOnboarding(backendConfig)).toBe(true);
		expect(
			render(OnBoarding, {
				props: { show: true },
				context: new Map([['i18n', writable({ t: (key: string) => key })]])
			}).body
		).toContain('Welcome to your AI home.');
	});
});
