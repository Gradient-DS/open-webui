// [Gradient] Sanitized model markup (raw HTML tables, Mermaid SVG) may still make the browser fetch from any host
// as soon as it renders: an image, a CSS url() or @import, a srcset. A prompt-injected answer can put chat content in
// such a URL, so these fetches are removed; links stay, they only go anywhere on a click.
import { externalHost } from './safeImageUrl';

const URL_ATTRIBUTES = new Set([
	'src',
	'href',
	'xlink:href',
	'srcset',
	'imagesrcset',
	'poster',
	'background',
	'data',
	'action',
	'formaction',
	'ping',
	'lowsrc',
	'dynsrc',
	'codebase',
	'manifest'
]);
const CSS_URL = /url\(\s*(['"]?)(.*?)\1\s*\)/gi;

const remote = (value: string): boolean => externalHost(value.trim()) !== null;

const stripped = (css: string): string =>
	css
		.replace(/@import[^;]*;?/gi, '')
		.replace(/(-webkit-)?image-set\([^)]*\)/gi, 'none')
		.replace(CSS_URL, (match, _quote, target: string) => (remote(target) ? 'none' : match));

const unescaped = (css: string): string =>
	css
		.replace(/\\([0-9a-f]{1,6})\s?/gi, (_, hex: string) =>
			String.fromCodePoint(Math.min(parseInt(hex, 16), 0x10ffff))
		)
		.replace(/\\(.)/gs, '$1');

/** CSS without remote loads: url() to another host, @import and image-set() go. A CSS escape could hide either, so
 * escaped CSS that would lose anything goes entirely. */
const localCss = (css: string): string => {
	if (!css.includes('\\')) return stripped(css);
	const plain = unescaped(css);
	return stripped(plain) === plain ? css : '';
};

/** The markup without anything that loads from another host on render. A remote picture becomes its alt text. */
export function withoutRemoteLoads(markup: string): string {
	const template = document.createElement('template');
	template.innerHTML = markup;
	for (const element of [...template.content.querySelectorAll('*')]) {
		const name = element.localName.toLowerCase();
		if (name === 'style') {
			element.textContent = localCss(element.textContent ?? '');
			continue;
		}
		if (['img', 'image'].includes(name)) {
			const source =
				element.getAttribute('src') ??
				element.getAttribute('href') ??
				element.getAttribute('xlink:href');
			if (source !== null && remote(source)) {
				element.replaceWith(document.createTextNode(element.getAttribute('alt') ?? ''));
				continue;
			}
		}
		for (const { name: attribute, value } of [...element.attributes]) {
			const key = attribute.toLowerCase();
			if (key === 'style') {
				const css = localCss(value);
				if (css) element.setAttribute(attribute, css);
				else element.removeAttribute(attribute);
			} else if (key === 'srcset' || key === 'imagesrcset' || key === 'ping') {
				element.removeAttribute(attribute);
			} else if (
				URL_ATTRIBUTES.has(key) &&
				remote(value) &&
				!(name === 'a' && key.endsWith('href'))
			) {
				element.removeAttribute(attribute);
			}
		}
	}
	return template.innerHTML;
}
