import { afterEach, describe, expect, it, vi } from 'vitest';
import { ancestorPaths, FolderUploadSession, mergeUploadRows } from './folderUpload';

const makeSession = (key = 'pick', paths = ['folder', 'folder/nested']) => {
	const callbacks = { onChange: vi.fn(), onRefresh: vi.fn(), onFinish: vi.fn() };
	const session = new FolderUploadSession(key, paths, callbacks);
	session.setDirectories({ folder: 'root', 'folder/nested': 'child' });
	return { session, ...callbacks };
};

afterEach(() => vi.useRealTimers());

describe('FolderUploadSession', () => {
	it('counts each ancestor and sums shared rows without combining session results', () => {
		vi.useFakeTimers();
		const first = makeSession('first');
		const second = makeSession('second', ['folder']);
		expect(ancestorPaths('folder/nested')).toEqual(['folder', 'folder/nested']);
		expect(first.session.placeholderId('folder')).not.toBe(second.session.placeholderId('folder'));
		first.session.onUploaded(['root'], { id: 'one' });
		first.session.onUploaded(['root', 'child'], { id: 'two' });
		second.session.onUploaded(['root'], { id: 'three' });
		first.session.onFileStatus('one', 'completed');
		expect(mergeUploadRows([first.session, second.session]).get('root')).toEqual({
			name: 'folder',
			total: 3,
			uploaded: 3,
			processed: 1,
			failed: 0
		});
		first.session.completeUploads();
		second.session.completeUploads();
		first.session.onFileStatus('two', 'failed');
		expect(first.onFinish).toHaveBeenCalledExactlyOnceWith(
			{ label: 'folder', added: 1, failed: 1, unresolved: 0 },
			false
		);
		expect(mergeUploadRows([first.session, second.session]).get('root')?.total).toBe(1);
		second.session.onFileStatus('three', 'completed');
		vi.runAllTimers();
		expect(first.onFinish).toHaveBeenCalledTimes(1);
		expect(second.onFinish).toHaveBeenCalledExactlyOnceWith(
			{ label: 'folder', added: 1, failed: 0, unresolved: 0 },
			false
		);
	});

	it('parks early events in every in-flight session and claims only its own file once', () => {
		vi.useFakeTimers();
		const first = makeSession('first', ['folder']);
		const second = makeSession('second', ['folder']);
		for (const { session } of [first, second]) {
			expect(session.onFileStatus('file', 'completed')).toBe(false);
		}
		first.session.onUploaded(['root'], { id: 'file' });
		expect(first.session.onFileStatus('file', 'completed')).toBe(true);
		expect(first.session.summary().added).toBe(1);
		expect(second.session.summary().added).toBe(0);
		first.session.completeUploads();
		expect(first.onFinish).toHaveBeenCalledTimes(1);
		second.session.dispose();
	});

	it('caps a session with an unresolved summary and finishes once', () => {
		vi.useFakeTimers();
		const { session, onFinish, onRefresh } = makeSession();
		session.onUploaded(['root'], { id: 'one' });
		session.onUploaded(['root', 'child'], { id: 'two' });
		session.onFileStatus('one', 'completed');
		session.completeUploads();
		vi.advanceTimersByTime(1500);
		expect(onRefresh).toHaveBeenCalledTimes(1);
		vi.advanceTimersByTime(10 * 60 * 1000);
		expect(onFinish).toHaveBeenCalledExactlyOnceWith(
			{ label: 'folder', added: 1, failed: 0, unresolved: 1 },
			true
		);
		expect(session.onFileStatus('two', 'completed')).toBe(false);
	});

	it('clears placeholder rows and pending timers when directory creation fails', () => {
		vi.useFakeTimers();
		const callbacks = { onChange: vi.fn(), onRefresh: vi.fn(), onFinish: vi.fn() };
		const session = new FolderUploadSession('pick', ['folder'], callbacks);
		expect(session.rows.has(session.placeholderId('folder'))).toBe(true);
		session.refreshSoon();
		session.completeUploads();
		session.dispose();
		expect(session.rows.size).toBe(0);
		expect(vi.getTimerCount()).toBe(0);
		vi.runAllTimers();
		expect(callbacks.onRefresh).not.toHaveBeenCalled();
		expect(callbacks.onFinish).not.toHaveBeenCalled();
	});

	it('schedules nothing and ignores late upload responses after disposal', () => {
		vi.useFakeTimers();
		const { session, onChange, onRefresh, onFinish } = makeSession();
		session.dispose();
		onChange.mockClear();
		session.refreshSoon();
		session.completeUploads();
		session.setDirectories({ folder: 'root' });
		session.onUploaded(['root'], { id: 'late' });
		expect(session.onFileStatus('late', 'failed')).toBe(false);
		expect(session.rows.size).toBe(0);
		expect(vi.getTimerCount()).toBe(0);
		expect(onChange).not.toHaveBeenCalled();
		expect(onRefresh).not.toHaveBeenCalled();
		expect(onFinish).not.toHaveBeenCalled();
	});
});
