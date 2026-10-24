import { describe, expect, it } from 'vitest';
import { parseCsv } from './csvTable';

describe('parseCsv', () => {
	it('reads a header and rows', () => {
		expect(parseCsv('maand,bezoekers\n2026-01,1463\n2026-02,1897\n')).toEqual({
			header: ['maand', 'bezoekers'],
			rows: [
				['2026-01', '1463'],
				['2026-02', '1897']
			],
			truncated: false
		});
	});

	it('takes semicolons when the header uses them', () => {
		expect(parseCsv('a;b\n1,5;2\n').rows).toEqual([['1,5', '2']]);
	});

	it('keeps quoted separators, doubled quotes and line breaks', () => {
		expect(parseCsv('a,b\r\n"x, y","say ""hi""\nthere"\r\n').rows).toEqual([
			['x, y', 'say "hi"\nthere']
		]);
	});

	it('stops after the row limit and says so', () => {
		const text = 'n\n' + Array.from({ length: 10 }, (_, i) => `${i}`).join('\n');
		const table = parseCsv(text, 3);
		expect(table.rows).toEqual([['0'], ['1'], ['2']]);
		expect(table.truncated).toBe(true);
	});
});
