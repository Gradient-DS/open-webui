import { describe, expect, it } from 'vitest';
import { fadeMask } from './overflowFade';

describe('fadeMask', () => {
	it('has no mask when everything fits', () => {
		expect(fadeMask(0, 200, 200)).toBe('');
	});

	it('fades the right edge when more is hidden there', () => {
		expect(fadeMask(0, 300, 200)).toBe(
			'linear-gradient(to right, black, black calc(100% - 1.5rem), transparent)'
		);
	});

	it('fades both edges in the middle and only the left at the end', () => {
		expect(fadeMask(50, 300, 200)).toContain('transparent, black 1.5rem');
		expect(fadeMask(50, 300, 200)).toContain('calc(100% - 1.5rem), transparent');
		expect(fadeMask(100, 300, 200)).toBe(
			'linear-gradient(to right, transparent, black 1.5rem, black)'
		);
	});
});
