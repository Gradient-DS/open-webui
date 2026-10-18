import { describe, expect, it } from 'vitest';
import { hasPages, pageSize, pageStyle } from './documentPages';

describe('document pages', () => {
	it('uses A4 portrait unless the document names another size', () => {
		expect(pageSize(undefined)).toEqual({ name: 'a4', width: 210, height: 297 });
		expect(pageSize('A4-Landscape')).toEqual({ name: 'a4-landscape', width: 297, height: 210 });
		expect(pageSize('tabloid')).toEqual({ name: 'a4', width: 210, height: 297 });
	});

	it('recognises page sections by their class', () => {
		expect(hasPages('<section class="page">1</section>')).toBe(true);
		expect(hasPages("<section id='p1' class='cover page dark'>1</section>")).toBe(true);
		expect(hasPages('<section class="pages">1</section><div class="page">2</div>')).toBe(false);
	});

	it('sizes page sections and leaves a flowing document as it was', () => {
		const a5 = pageSize('a5');
		const paged = pageStyle('<section class="page"></section>', a5, 'paper');
		expect(paged).toContain('@page { size: 148mm 210mm; margin: 0; }');
		expect(paged).toContain('width: 148mm !important; height: 210mm !important;');
		expect(paged).not.toContain('box-shadow');
		expect(pageStyle('<section class="page"></section>', a5, 'screen')).toContain('box-shadow');
		expect(pageStyle('<main>flowing</main>', a5, 'screen')).toBe('');
	});
});
