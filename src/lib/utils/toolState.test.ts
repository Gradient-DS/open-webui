import { describe, it, expect } from 'vitest';
import {
	BINARY_TOOL_STATES,
	DOCUMENT_WRITER_STATES,
	documentWriterFlags,
	documentWriterState,
	WEB_SEARCH_STATES,
	nextToolState,
	webSearchFlags,
	webSearchState
} from './toolState';

describe('nextToolState', () => {
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

describe('PDF writer flags', () => {
	it('cycles Auto, Always, Off and round-trips the request flags', () => {
		expect(DOCUMENT_WRITER_STATES).toEqual(['auto', 'required', 'off']);
		for (const [index, state] of DOCUMENT_WRITER_STATES.entries()) {
			expect(nextToolState(state, DOCUMENT_WRITER_STATES)).toBe(
				DOCUMENT_WRITER_STATES[(index + 1) % 3]
			);
			const { enabled, required } = documentWriterFlags(state);
			expect(enabled).toBe(state !== 'off');
			expect(required).toBe(state === 'required');
			expect(documentWriterState(enabled, required)).toBe(state);
		}
	});
	it('reads enabled-only saved state as Auto and ignores required when off', () => {
		expect(documentWriterState(true, false)).toBe('auto');
		expect(documentWriterState(false, true)).toBe('off');
	});
});
