import { describe, it, expect } from 'vitest';
import { buildSyncToast } from './syncToast';

// Minimal i18n stub — i18next's `t(key, opts)` interpolates `{{count}}` and
// picks the `_one`/`_other` key suffix. For the toast formatter we only care
// that the string contains the count and the segment label, so we mock the
// resolver to a key + interpolation echo.
const i18nStub = {
	t: (key: string, opts: { count?: number } = {}) =>
		opts.count !== undefined ? key.replace('{{count}}', String(opts.count)) : key
} as unknown as Parameters<typeof buildSyncToast>[0];

describe('buildSyncToast', () => {
	it('returns info "No changes" when every segment is zero', () => {
		const result = buildSyncToast(i18nStub, 'Google Drive', {
			added: 0,
			updated: 0,
			unchanged: 12,
			failed: 0,
			removed: 0
		});
		expect(result.variant).toBe('info');
		expect(result.message).toBe('Google Drive: No changes');
	});

	it('renders a single "Added N" segment for a fresh sync', () => {
		const result = buildSyncToast(i18nStub, 'Google Drive', { added: 5 });
		expect(result.variant).toBe('success');
		expect(result.message).toContain('Added 5');
	});

	it('renders Added + failed as warning', () => {
		const result = buildSyncToast(i18nStub, 'Google Drive', {
			added: 12,
			failed: 2
		});
		expect(result.variant).toBe('warning');
		expect(result.message).toContain('Added 12');
		expect(result.message).toContain('2 failed');
	});

	it('renders all-failed as error', () => {
		const result = buildSyncToast(i18nStub, 'OneDrive', { failed: 3 });
		expect(result.variant).toBe('error');
		expect(result.message).toContain('3 failed');
	});

	it('renders added + updated + removed in one message', () => {
		const result = buildSyncToast(i18nStub, 'OneDrive', {
			added: 1,
			updated: 1,
			removed: 1
		});
		expect(result.variant).toBe('success');
		expect(result.message).toContain('Added 1');
		expect(result.message).toContain('Updated 1');
		expect(result.message).toContain('Removed 1');
	});

	it('omits the leading "Label: " prefix when label is null (local upload)', () => {
		const result = buildSyncToast(i18nStub, null, { added: 3 });
		expect(result.message.startsWith('Label')).toBe(false);
		expect(result.message).toBe('Added 3');
	});
});
