import { describe, expect, it } from 'vitest';
import { render } from 'svelte/server';
import SourceItemIcon from './SourceItemIcon.svelte';

describe('source item icon', () => {
	it.each(['onedrive', 'google_drive', 'confluence'])('badges a %s item', (provider) => {
		const html = render(SourceItemIcon, { props: { kind: 'file', provider } }).body;
		expect(html).toContain(`data-provider="${provider}"`);
		expect(html.match(/<svg/g)).toHaveLength(2);
	});
	it.each([null, 'local', 'unknown', 'toString'])('leaves a %s item unbadged', (provider) => {
		const html = render(SourceItemIcon, { props: { kind: 'folder', provider } }).body;
		expect(html).not.toContain('data-provider');
		expect(html.match(/<svg/g)).toHaveLength(1);
	});
	it('distinguishes folders from files', () => {
		const folder = render(SourceItemIcon, { props: { kind: 'folder' } }).body;
		const file = render(SourceItemIcon, { props: { kind: 'file' } }).body;
		expect(folder).not.toBe(file);
	});
});
