import { describe, it, expect } from 'vitest';
import { liveDocumentState, toolStateLabel } from './toolState';

describe('liveDocumentState', () => {
	it('reads Auto as on and anything else as off', () => {
		expect(liveDocumentState('auto')).toBe('auto');
		expect(liveDocumentState('off')).toBe('off');
		expect(liveDocumentState(undefined)).toBe('off');
		expect(liveDocumentState(true)).toBe('off');
	});
	it('reads a saved Altijd as on', () => {
		expect(liveDocumentState('required')).toBe('auto');
	});
});

describe('toolStateLabel', () => {
	it('labels a tool On or Off', () => {
		expect(toolStateLabel(true)).toBe('On');
		expect(toolStateLabel(false)).toBe('Off');
	});
});
