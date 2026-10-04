import { describe, expect, it } from 'vitest';

import { markupSafeEnd, maskInFlightTag } from './streamMarkup';

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

describe('markupSafeEnd', () => {
	it('keeps plain text where it is', () => {
		expect(markupSafeEnd('Gewone tekst zonder opmaak.', 10)).toBe(10);
	});

	it('shows a complete details block whole and holds one still being written', () => {
		const block =
			'Voor <details type="tool_calls" done="true"><summary>Read</summary></details> na';
		expect(markupSafeEnd(block, 12)).toBe(block.indexOf(' na'));
		expect(markupSafeEnd('Voor <details type="tool_calls"><summary>Re', 30)).toBe(5);
	});

	it('holds a tag until its closing angle bracket arrives', () => {
		expect(markupSafeEnd('tekst <document title="Ges', 26)).toBe(6);
		expect(markupSafeEnd('tekst <br> verder', 8)).toBe(10);
	});

	it('reveals bold and inline code with their closing marks', () => {
		const text = 'Over **soev.ai** en `code` hier';
		expect(markupSafeEnd(text, 9)).toBe(text.indexOf(' en'));
		expect(markupSafeEnd(text, 22)).toBe(text.indexOf(' hier'));
		expect(markupSafeEnd('Over **soev', 11)).toBe(5);
	});

	it('reveals a citation marker whole', () => {
		expect(markupSafeEnd('zie bron [12] verder', 11)).toBe(13);
		expect(markupSafeEnd('zie bron [1', 11)).toBe(9);
	});

	it('does not split a surrogate pair', () => {
		expect(markupSafeEnd('ok 😀 ja', 4)).toBe(5);
	});

	it('ignores a less-than sign that does not start a tag', () => {
		expect(markupSafeEnd('als x < 5 dan', 9)).toBe(9);
	});
});

it('holds every partial agent document until its closing details arrives', () => {
	const marker =
		'<details type="document" format="html" title="Report" done="true"><summary>Document</summary>\n&lt;h1&gt;Report&lt;/h1&gt;\n</details>';
	for (let end = 1; end < marker.length; end++) {
		const partial = 'Before ' + marker.slice(0, end);
		const masked = maskInFlightTag(partial);
		expect(markupSafeEnd(masked, masked.length)).toBeLessThanOrEqual(7);
	}
	expect(markupSafeEnd('Before ' + marker, 20)).toBe(7 + marker.length);
});
