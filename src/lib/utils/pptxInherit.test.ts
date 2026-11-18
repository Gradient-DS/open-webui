// @vitest-environment jsdom
import { describe, expect, it } from 'vitest';

import { inheritPlaceholders } from './pptxInherit';

const NS =
	'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"';
const parse = (root: string, body: string) =>
	new DOMParser().parseFromString(`<p:${root} ${NS}>${body}</p:${root}>`, 'application/xml');
const shape = (ph: string, spPr: string, txBody: string) =>
	`<p:sp><p:nvSpPr><p:nvPr><p:ph ${ph}/></p:nvPr></p:nvSpPr><p:spPr>${spPr}</p:spPr>` +
	`<p:txBody>${txBody}</p:txBody></p:sp>`;
const EMPTY = '<a:bodyPr/><a:lstStyle/><a:p><a:endParaRPr lang="en-GB"/></a:p>';

const layout = () =>
	parse(
		'sldLayout',
		shape(
			'type="ctrTitle" hasCustomPrompt="1"',
			'<a:solidFill><a:srgbClr val="EEEEEE"/></a:solidFill>',
			'<a:bodyPr anchor="b"/><a:lstStyle><a:lvl1pPr algn="l"><a:defRPr sz="6000"/></a:lvl1pPr></a:lstStyle>' +
				'<a:p><a:r><a:t>Title</a:t></a:r></a:p>'
		) +
			shape(
				'type="body" idx="1"',
				'',
				'<a:bodyPr/><a:lstStyle/><a:p><a:r><a:t>Click to edit Master text styles</a:t></a:r></a:p>'
			)
	);
const master = () =>
	parse(
		'sldMaster',
		shape('type="title"', '', '<a:bodyPr lIns="0" anchor="t"/><a:lstStyle/><a:p/>') +
			'<p:txStyles><p:titleStyle><a:lvl1pPr algn="ctr"><a:defRPr sz="3600" b="1">' +
			'<a:solidFill><a:schemeClr val="accent1"/></a:solidFill></a:defRPr></a:lvl1pPr></p:titleStyle>' +
			'<p:bodyStyle><a:lvl1pPr><a:buChar char="•"/><a:defRPr sz="1600"/></a:lvl1pPr></p:bodyStyle></p:txStyles>'
	);
const slide = (title = EMPTY) =>
	parse('sld', shape('type="ctrTitle"', '', title) + shape('type="body" idx="1"', '', EMPTY));
const titleOf = (doc: Document) => doc.getElementsByTagName('p:sp')[0];
const texts = (el: Element) =>
	Array.from(el.getElementsByTagName('a:t')).map((text) => text.textContent);

describe('inheritPlaceholders', () => {
	it('shows a template the prompts its author wrote, never the stock ones', () => {
		const doc = slide();
		inheritPlaceholders(doc, layout(), master(), true);
		expect(texts(doc.documentElement)).toEqual(['Title']);
	});
	it('leaves empty placeholders empty in a presentation', () => {
		const doc = slide();
		inheritPlaceholders(doc, layout(), master(), false);
		expect(texts(doc.documentElement)).toEqual([]);
	});
	it('takes each style from the nearest of layout and master', () => {
		const doc = slide('<a:bodyPr/><a:lstStyle/><a:p><a:r><a:t>Hello</a:t></a:r></a:p>');
		inheritPlaceholders(doc, layout(), master(), true);
		const title = titleOf(doc);
		const pPr = title.getElementsByTagName('a:pPr')[0];
		const defRPr = pPr.getElementsByTagName('a:defRPr')[0];
		expect(texts(title)).toEqual(['Hello']);
		expect(pPr.getAttribute('algn')).toBe('l');
		expect([defRPr.getAttribute('sz'), defRPr.getAttribute('b')]).toEqual(['6000', '1']);
		expect(defRPr.getElementsByTagName('a:schemeClr')[0].getAttribute('val')).toBe('accent1');
		const bodyPr = title.getElementsByTagName('a:bodyPr')[0];
		expect([bodyPr.getAttribute('anchor'), bodyPr.getAttribute('lIns')]).toEqual(['b', '0']);
		expect(title.getElementsByTagName('a:srgbClr')[0].getAttribute('val')).toBe('EEEEEE');
	});
	it('keeps what the slide sets itself', () => {
		const doc = slide(
			'<a:bodyPr anchor="ctr"/><a:lstStyle/><a:p><a:pPr algn="r"/><a:r><a:t>Hello</a:t></a:r></a:p>'
		);
		inheritPlaceholders(doc, layout(), master(), true);
		const title = titleOf(doc);
		expect(title.getElementsByTagName('a:pPr')[0].getAttribute('algn')).toBe('r');
		expect(title.getElementsByTagName('a:bodyPr')[0].getAttribute('anchor')).toBe('ctr');
	});
	it('gives body text the bullet of the master', () => {
		const doc = parse(
			'sld',
			shape(
				'type="body" idx="1"',
				'',
				'<a:bodyPr/><a:lstStyle/><a:p><a:r><a:t>Point</a:t></a:r></a:p>'
			)
		);
		inheritPlaceholders(doc, layout(), master(), false);
		expect(doc.getElementsByTagName('a:buChar')[0].getAttribute('char')).toBe('•');
	});
});
