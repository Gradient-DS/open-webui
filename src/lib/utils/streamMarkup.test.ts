import { describe, expect, it } from 'vitest';

import { maskInFlightTag } from './streamMarkup';

describe('maskInFlightTag', () => {
	it('hides a Document Writer opening tag until its ">" arrives', () => {
		expect(maskInFlightTag('<document title="Geschiedenis van de fiets in')).toBe('');
		expect(maskInFlightTag('<document title="X">body')).toBe('<document title="X">body');
	});

	it('hides a partially streamed tool_calls anchor', () => {
		expect(maskInFlightTag('<details type="tool_calls" done="true" name="read_doc')).toBe('');
	});

	it('keeps the prose that precedes the in-flight tag', () => {
		expect(maskInFlightTag('Hier is het:\n\n<document title="X')).toBe('Hier is het:\n\n');
	});

	it('hides a closing tag that is still arriving', () => {
		expect(maskInFlightTag('tekst </deta')).toBe('tekst ');
	});

	it('leaves a completed block alone', () => {
		const done = '<details type="tool_calls" done="true" name="read_document">x</details>';
		expect(maskInFlightTag(done)).toBe(done);
	});

	it('leaves prose, autolinks and unrelated tags alone', () => {
		expect(maskInFlightTag('5 < 3 is waar')).toBe('5 < 3 is waar');
		expect(maskInFlightTag('5 <3')).toBe('5 <3');
		expect(maskInFlightTag('zie <https://example.com')).toBe('zie <https://example.com');
		expect(maskInFlightTag('een <div')).toBe('een <div');
		expect(maskInFlightTag('normale tekst zonder tags')).toBe('normale tekst zonder tags');
	});
});
