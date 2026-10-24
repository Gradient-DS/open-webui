import { describe, expect, it } from 'vitest';

import { clock } from './clock';

describe('clock', () => {
	it.each([
		[0, '0:00'],
		[7.9, '0:07'],
		[252, '4:12'],
		[3725, '1:02:05'],
		[-3, '0:00']
	])('%s seconds read %s', (seconds, shown) => {
		expect(clock(seconds)).toBe(shown);
	});
});
