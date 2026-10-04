import { describe, it, expect } from 'vitest';
import {
	BINARY_TOOL_STATES,
	LIVE_DOCUMENT_STATES,
	WEB_SEARCH_STATES,
	nextToolState,
	webSearchFlags,
	webSearchState
} from './toolState';

describe('nextToolState', () => {
	it('cycles OneDrive search off and auto without requiring a search', () => {
		expect(nextToolState('off', LIVE_DOCUMENT_STATES)).toBe('auto');
		expect(nextToolState('auto', LIVE_DOCUMENT_STATES)).toBe('off');
	});
	it('cycles web search Auto, Altijd, Uit and back', () => {
		expect(nextToolState('auto', WEB_SEARCH_STATES)).toBe('required');
		expect(nextToolState('required', WEB_SEARCH_STATES)).toBe('off');
		expect(nextToolState('off', WEB_SEARCH_STATES)).toBe('auto');
	});
	it('cycles two-state tools between Uit and Altijd', () => {
		expect(nextToolState('off', BINARY_TOOL_STATES)).toBe('required');
		expect(nextToolState('required', BINARY_TOOL_STATES)).toBe('off');
	});
	it('starts the cycle from a state the tool does not have', () => {
		expect(nextToolState('auto', BINARY_TOOL_STATES)).toBe('off');
	});
});

describe('web search flags', () => {
	it('round-trips every state', () => {
		for (const state of WEB_SEARCH_STATES) {
			const { enabled, required } = webSearchFlags(state);
			expect(webSearchState(enabled, required)).toBe(state);
		}
	});
	it('reads an old enabled-only payload as Auto', () => {
		expect(webSearchState(true, false)).toBe('auto');
	});
	it('ignores required while web search is off', () => {
		expect(webSearchState(false, true)).toBe('off');
	});
});
