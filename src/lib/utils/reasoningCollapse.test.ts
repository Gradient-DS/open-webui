// @vitest-environment jsdom
import { describe, expect, it } from 'vitest';
import { collapsesOnClick } from './reasoningCollapse';

const inside = (html: string, selector: string) => {
	const root = document.createElement('div');
	root.innerHTML = html;
	return root.querySelector(selector);
};

describe('collapsesOnClick', () => {
	it('collapses on a click in plain reasoning text', () => {
		expect(collapsesOnClick(inside('<p>Eerst lees ik <em>het verslag</em></p>', 'em'), '')).toBe(
			true
		);
	});

	it('stays open while the reader selects text', () => {
		expect(collapsesOnClick(inside('<p>Eerst lees ik het verslag</p>', 'p'), 'het verslag')).toBe(
			false
		);
	});

	it('stays open for links, buttons and code blocks', () => {
		expect(collapsesOnClick(inside('<p><a href="#"><span>bron</span></a></p>', 'span'), '')).toBe(
			false
		);
		expect(collapsesOnClick(inside('<div><button>Kopieer</button></div>', 'button'), '')).toBe(
			false
		);
		expect(collapsesOnClick(inside('<pre><code>arr[1]</code></pre>', 'code'), '')).toBe(false);
	});

	it('collapses when the target is not an element', () => {
		expect(collapsesOnClick(null, '')).toBe(true);
	});
});
