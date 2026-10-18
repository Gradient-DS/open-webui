import { describe, expect, it } from 'vitest';
import type { SchedulePair } from '../utils/cloudSync';
import { isSynced, withoutLooseSourceFiles } from './syncedFiles';

const file = (id: string, schedules?: string[]) => ({
	id,
	meta: schedules ? { soev_schedule_ids: schedules } : {}
});
const pair = (id: string) =>
	({ content: { id }, acl: { id: `${id}-acl` } }) as unknown as SchedulePair;

describe('synced files', () => {
	it('marks only files a schedule reaches', () => {
		expect(isSynced(file('a', ['s']))).toBe(true);
		expect(isSynced(file('b', []))).toBe(false);
		expect(isSynced(file('c'))).toBe(false);
	});

	it('drops a single-file source file once its source row lists it', () => {
		const files = [file('local'), file('single', ['one']), file('both', ['one', 'folder'])];
		const listed = withoutLooseSourceFiles(files, [pair('one')]);
		expect(listed.map((f) => f.id)).toEqual(['local', 'both']);
		expect(withoutLooseSourceFiles(files, []).map((f) => f.id)).toEqual([
			'local',
			'single',
			'both'
		]);
	});
});
