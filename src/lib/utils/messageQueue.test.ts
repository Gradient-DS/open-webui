import { describe, expect, it } from 'vitest';
import { applyFileStatus, applyUploadResult, isAwaitingUpload } from './messageQueue';

const uploading = { type: 'file', id: 'f1', itemId: 'i1', name: 'a.docx', status: 'uploading' };
const queued = (files) => ({ c1: [{ id: 'q1', prompt: 'vat samen', files }] });

describe('message queue', () => {
	it('waits while a file is uploading or processing', () => {
		expect(isAwaitingUpload([uploading])).toBe(true);
	});

	it('releases a queued message once its file finished processing', () => {
		const queues = applyFileStatus(queued([uploading]), {
			file_id: 'f1',
			status: 'completed',
			collection_name: 'owui-attachments-u'
		});
		expect(queues.c1[0].files[0]).toMatchObject({
			status: 'uploaded',
			collection_name: 'owui-attachments-u'
		});
		expect(isAwaitingUpload(queues.c1[0].files)).toBe(false);
	});

	it('releases a queued message whose file failed, keeping it for the skipped notice', () => {
		const queues = applyFileStatus(queued([uploading]), {
			file_id: 'f1',
			status: 'failed',
			error: 'boom'
		});
		expect(queues.c1[0].files[0]).toMatchObject({ id: 'f1', status: 'failed', error: 'boom' });
		expect(isAwaitingUpload(queues.c1[0].files)).toBe(false);
	});

	it('keeps waiting on a processing event', () => {
		const queues = applyFileStatus(queued([uploading]), { file_id: 'f1', status: 'processing' });
		expect(isAwaitingUpload(queues.c1[0].files)).toBe(true);
	});

	it('leaves queues without the file untouched', () => {
		const queues = queued([uploading]);
		expect(applyFileStatus(queues, { file_id: 'other', status: 'completed' })).toBe(queues);
	});

	it('takes the upload response for the matching item', () => {
		const pending = { ...uploading, id: null };
		const [message] = applyUploadResult(queued([pending]).c1, uploading);
		expect(message.files[0]).toBe(uploading);
		expect(isAwaitingUpload(message.files)).toBe(true);
	});

	it('drops a file whose upload failed outright so the message still sends', () => {
		const [message] = applyUploadResult(queued([uploading]).c1, {
			...uploading,
			id: null,
			status: 'error'
		});
		expect(message.files).toEqual([]);
		expect(isAwaitingUpload(message.files)).toBe(false);
	});
});
